#!/usr/bin/env python

"""
@file sync
@brief Synchronize a GitHub organization's membership to a Team
@details
GitHub does not create a team that automatically includes all of
the organization's members.  Because collaborators are either
individuals or teams but not organizations, there is no native
way to grant all members of an organization access to, for example,
an internal repository.

Therefore, this script will create and populate a team in an
organization that has as its membership the members of the
organization.  This allows folks to provide access to a team
that represents everyone in the organization.  For example:

    @my-organization/everyone
"""

import json
import logging
import os
import re
import sys
import time

from dotenv import load_dotenv
from github import Github

load_dotenv()

##
# @var str PAT
# @brief Personal Access Token for interacting with GitHub
PAT = str(os.getenv("PAT", None))

##
# @var str ORG
# @brief name of GitHub organization to query
ORG = str(os.getenv("ORG", None))

##
# @var str TEAM_NAME
# @brief the name of the team to process
TEAM_NAME = os.getenv("TEAM_NAME", None)

##
# @var str DRY_RUN
# @brief when False, perform read-write operations; otherwise, read-only
DRY_RUN = os.getenv("DRY_RUN", "True")

##
# @var str API_URL
# @brief the URL to the API; default is GitHub.com's API
API_URL = os.getenv("API_URL", "https://api.github.com")

##
# @var str USER_FILTERS
# @brief a JSON object describing the filters to apply to determine membership
USER_FILTERS = json.loads(os.getenv("USER_FILTERS", "[]"))

##
# @var float DELAY
# @brief the number of seconds to wait between API calls
DELAY = float(os.getenv("DELAY", "3"))

##
# @var int LOG_LEVEL
# @brief the threshold for displaying logs; higher is quieter
LOG_LEVEL = int(os.getenv("LOG_LEVEL", "20"))

logging.basicConfig(level=LOG_LEVEL)

if PAT is None:
    logging.critical("PAT was undefined")
    sys.exit(1)

if ORG is None:
    logging.critical("ORG was undefined")
    sys.exit(1)

if TEAM_NAME is None:
    logging.critical("TEAM_NAME was undefined")
    sys.exit(1)


def create_team_if_not_exists(team_name, organization):
    """
    @fn create_team_if_not_exists()
    @brief if a team doesn't exist, create it; either way, return it
    @details
    Given an organization and the name of a team, check to see if a
    team with that name exists in the organization.  If it exists,
    return a team object immediately; if it does not exist, create
    the team (requires admin:org scope) and then return a team object
    @param team_name the name of the team (just the name, not the org)
    @param organization the organization object
    @returns team object
    @par Example
    @code
    my_team = create_team_if_not_exists("my_team", my_org)
    @endcode
    """

    logging.debug("Fetching teams from organization")
    teams = organization.get_teams()

    for team in teams:
        logging.debug(
            'Comparing requested "%s" with detected "%s"', team_name, team.name
        )
        if team.name == team_name:
            logging.debug("Found a match")
            return team

    logging.info('Team "%s" was not found; creating it.', team_name)
    return organization.create_team(team_name)


def get_group_logins(group):
    """
    @fn get_group_logins()
    @brief given a team or an organization, return a dict of its members
    @details
    This will iterate through the list of members in either a team or
    an organization and return a dict with each member's login as the
    key and True as the value.
    @param group the team or organization object to query
    @returns dict with the members' logins as keys
    @par Examples
    @code
    team_members = get_group_logins(team)
    org_members = get_group_logins(organization)
    @endcode
    """

    member_logins = {}

    for member in group.get_members():
        logging.debug('Found member "%s"', member.login)

        member_logins[member.login] = True

    return member_logins


def allow_user(member):
    """
    @fn allow_user()
    @brief determine if a user should be filtered out
    @details
    We may wish to filter certain users out of a team.  This will
    accept an organizational member and apply a series of rules
    defined in the configuration to see if that user should be
    allowed in to the team.

    First, the we look at the 'login' dictionary for an item
    named 'login'.

    For 'login', we look for a list named 'reject'.  Items in
    the 'reject' list are regular expressions matched against
    the 'member.login' field.  If a member's login matches the
    regular expression, they're not included in the list of
    members allowed in to the group.

    However, we also look for a list named 'allow' which is
    also a group of regular expressions.  If a user matches
    the 'reject' list and the 'allow' list, they are allowed.

    So, at a high level, it looks like this:

    1. accept all users into the group by default
    2. if a user matches a reject regex, they're rejected
    3. if a user matches an allow regex, they're allowed

    For example, suppose a member's login is 'wes' and the
    'USER_FILTERS' dictionary looks like this:

    ```JSON
    {
      "login": {
        "reject": [
          "^w.*"
        ],
        "allow": [
          "s$"
        ]
      }
    }
    ```

    In this case, "wes" matches "^w" so it would be
    rejected; however, it also matches "s$", so it
    would be allowed.  The end result would be True
    (allow the user).

    If the member's login was 'wanda', it would
    match the "^w" regex (reject) but not the
    "s$" reject (allow).  The end result would
    be False (deny the user).

    If the member's login was 'jess', it would
    not match the "^w" regex (reject) so they
    would be allowed (the "s$" regex wouldn't
    be applied).

    @param member the member object to consider
    @retval True accept the user into the team
    @retval False deny the user membership into the team
    @retval None no finding for this user
    @par Examples
    @code
    for member in organizational_members:
        if allow_user(member):
            print(f"We like {member.login}!")
    @endcode
    """
    allow_this_user = None

    for field in dict(sorted(USER_FILTERS.items(), key=user_filter_order)):
        logging.debug("Examining %s for inclusion", member.login)

        if matches_regexes(member, field, "reject"):
            allow_this_user = False

            if matches_regexes(member, field, "allow"):
                allow_this_user = True

    logging.debug("    filter result: %s", allow_this_user)

    return allow_this_user


def user_filter_order(item):
    """
    @fn user_filter_order()
    @brief determine the order of USER_FIELDS to process
    @details
    The `USER_FIELDS` dictionary may include `order` fields
    which determine the order in which fields are processed
    with lower orders processed before higher orders.  This
    will return the order for fields.  The default `order`
    is `0`.
    @param item the dict under `USER_FIELDS` to consider
    @returns the order value or 0 if undefined
    @par Examples
    @code
    sorted_fields = sorted(MY_FIELDS.items(), key=user_filter_order)
    @endcode
    """
    if "order" in item:
        return item[1]["order"]

    return 0


def matches_regexes(member, field, key):
    """
    @fn matches_regex()
    @brief determines if a user matches filter criteria
    @details
    This will iterate across all of the fields for a user
    and see if they match the requested key's regexes.

    Currently, the only field we support is 'login' because
    the only field we can access (without querying the API's
    user endpoint) is.... 'login'.

    It may be possible to expand that login in the future
    (e.g., if we're checking something other than 'login',
    query the API) but for now, we're not doing that.

    The logic of "allow everyone by default, then reject
    the rejected users, then allow the allowed users is
    outside of the scope for this method; it's possible
    to reverse the logic (e.g., reject everyone by
    default, then only allow the people in the allow
    list, then reject those in the reject list); this
    method would be the same in either case, so that
    logic is found in allow_user() instead.
    @param member the member to examine
    @param field the USER_FILTER key to reference
    @param key either reject or allow
    @retval True if the member matches the regexes
    @retval False if the member doesn't match the regexes
    @par Examples
    @code
    if matches_regexes(member, 'login', 'reject'):
        printf("%s" is rejected", member.login)
    @endcode
    """

    if field in USER_FILTERS:
        if key in USER_FILTERS[field]:
            for regex in USER_FILTERS[field][key]:
                logging.debug("    Applying %s.%s to regex '%s'", field, key, regex)
                this_field = getattr(member, field)
                if re.search(regex, this_field, re.IGNORECASE):
                    logging.debug("  User matches %s regex", key)
                    return True

    return False


def is_dry_run(value=DRY_RUN):
    """
    @fn is_dry_run()
    @brief determine if we're running in dry run or not
    @details
    A "dry run" is one where we don't actually make any
    changes and it's determined by the global `DRY_RUN`.
    When `DRY_RUN` is set to `False` or `no`, then we'll
    return False (perform write operations); when it's
    set to `True` or `yes, we return `True` (do NOT
    perform write operations); if it's set to anything else,
    we return `True` (do NOT perform write operations).

    By default, we return True (don't perform write
    operations) just to be safe.
    @param value the value to evaluate (default: DRY_RUN)
    @retval False perform write operations
    @retval True don't perform write operations
    @par Examples
    @code
    if is_dry_run():
        print("We won't do the thing")
    else:
        print("We're going to do the thing!!!")
    @endcode
    """

    normalized_value = value.lower()

    if normalized_value in ("true", "yes"):
        return True
    if normalized_value in ("false", "no"):
        return False

    return True


def add_member_to_team(member_object, team_object):
    """
    @fn add_member_to_team()
    @brief add a member to a team
    @details
    This will add a member object to a team object
    @param member_object the object that represents the user
    @param team_object the object that represents the team
    @retval True if the addition was successful
    @retval False if the addition failed
    @retval None otherwise (e.g., dry run)
    @par Examples
    @code
    add_member_to_team(wes_object, awesome_people_team_object)
    @endcode
    """

    team_name = team_object.name
    member_name = member_object.login

    logging.info("Received member '%s' to add to team '%s'", member_name, team_name)

    if is_dry_run():
        logging.debug("In DRY_RUN, so not interacting with GitHub API")
        return None

    logging.debug("Not in DRY_RUN, so interacting with GitHub API")
    return team_object.add_membership(member_object)


def remove_member_from_team(member_object, team_object):
    """
    @fn remove_member_from_team()
    @brief remove a member from a team
    @details
    This will remove a member object from a team object.  It's the
    opposite of add_member_to_team().
    @param member_object the object that represents the user
    @param team_object the object that represents the team
    @retval True if the removal was successful
    @retval False if the removal failed
    @retval None otherwise (e.g., dry run)
    @par Examples
    @code
    remove_member_from_team(bad_guy_object, winners_team_object)
    @endcode
    """

    team_name = team_object.name
    member_name = member_object.login

    logging.info(
        "Received member '%s' to remove from team '%s'", member_name, team_name
    )

    if is_dry_run():
        logging.debug("In DRY_RUN, so not interacting with GitHub API")
        return None

    logging.debug("Not in DRY_RUN, so interacting with GitHub API")
    return team_object.remove_member(member_name)


def main():
    """
    @fn main()
    @brief the main function
    """

    logging.debug('Using PAT "%s**************************"', PAT[1:8])
    logging.debug('Using ORG "%s"', ORG)
    logging.debug('Using TEAM_NAME "%s"', TEAM_NAME)

    github = Github(login_or_token=PAT, base_url=API_URL)

    organization_object = github.get_organization(ORG)

    team_object = create_team_if_not_exists(TEAM_NAME, organization_object)

    current_org_members = get_group_logins(organization_object)
    current_team_members = get_group_logins(team_object)

    member_count = 0
    member_total = len(current_org_members)

    for member in current_org_members:
        member_object = github.get_user(member)
        member_count += 1

        logging.info("%i / %i: %s", member_count, member_total, member)

        if (
            member in current_team_members
            and current_team_members[member]
            and allow_user(member_object) is False
        ):
            logging.info(
                "'%s' is in the team but shouldn't be, so removing them", member
            )
            remove_member_from_team(member_object, team_object)
        elif member not in current_team_members and allow_user(member_object) in (
            None,
            True,
        ):
            logging.info("'%s' is in the org but not the team, so adding them", member)
            add_member_to_team(member_object, team_object)
        else:
            logging.info("No action required for '%s'", member)

        if member_count != member_total:
            time.sleep(DELAY)


if __name__ == "__main__":
    main()
