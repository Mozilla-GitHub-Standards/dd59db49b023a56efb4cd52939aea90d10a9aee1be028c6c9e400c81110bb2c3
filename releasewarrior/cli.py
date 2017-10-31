import os
import collections
import json
from copy import deepcopy

import click
import arrow
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from git import Repo

from releasewarrior.helpers import get_config, load_json
from releasewarrior.helpers import get_logger


LOGGER = get_logger()
CONFIG = get_config()

Release = collections.namedtuple('Release', 'product, version, branch, date')
Prerequisite = collections.namedtuple('Prerequisite', 'bug, deadline, description, resolved')


def get_branch(version):
    # TODO
    return "beta"


def validate(release, logger, config):
    # TODO validate version
    # TODO check if local release-pipeline repo is dirty
    # TODO sanity check if local release-pipeline repo is behind upstream
    pass

def validate_track(release, logger, config):
    validate(release, logger, config)
    # TODO ensure release doesn't already exist
    pass

def generate_tracking_data(release, logger, config):
    logger.info("generating data from template and config")

    data_template = os.path.join(
        config['templates_dir'],
        config['templates']["data"][release.product][release.branch]
    )

    data = load_json(data_template)

    data["version"] = release.version
    data["date"] = release.date

    return data


def generate_wiki(data, release, logger, config):
    logger.info("generating wiki from template and config")

    # TODO convert issues to bugs

    wiki_template = config['templates']["wiki"][release.product][release.branch]

    env = Environment(loader=FileSystemLoader(config['templates_dir']),
                      undefined=StrictUndefined, trim_blocks=True)

    template = env.get_template(wiki_template)
    return template.render(**data)


def write_data(data_path, content, release, logger, config):
    logger.info("writing to data file: %s", data_path)
    with open(data_path, 'w') as data_file:
        json.dump(content, data_file, indent=4, sort_keys=True)

    return data_path


def write_wiki(wiki_path, content, release, logger, config):
    logger.info("writing to wiki file: %s", wiki_path)
    with open(wiki_path, 'w') as wp:
        wp.write(content)

    return wiki_path


def commit(files, msg, logger, config):
    logger.info("committing changes with message: %s", msg)

    repo = Repo(config['release_pipeline_repo'])
    repo.index.add(files)

    if not repo.index.diff("HEAD"):
        logger.fatal("nothing staged for commit. has the data or wiki file changed?")

    commit = repo.index.commit(msg)
    for patch in repo.commit("HEAD~1").diff(commit, create_patch=True):
        logger.info(patch)


def generate_prereq_from_input():
    bug = click.prompt('Bug number if exists', type=str, default="no bug")
    description = click.prompt('Description of prerequisite task', type=str)
    deadline = click.prompt('When does this have to be completed', type=str,
                            default=arrow.now('US/Pacific').format("YYYY-MM-DD"))
    return Prerequisite(bug, deadline, description, resolved=False)


def update_prereq_tasks(data, resolve):
    data = deepcopy(data)
    if resolve:
        for _id in resolve:
            # 0 based index so -1
            _id = int(_id) - 1
            data["preflight"]["human_tasks"][int(id)]["resolved"] = True
    else:
        # create a new prerequisite task through interactive inputs
        new_prereq = generate_prereq_from_input()
        data["preflight"]["human_tasks"].append(
            {
                "bug": new_prereq.bug, "deadline": new_prereq.deadline,
                "description": new_prereq.description, "resolved": False
            }
        )
    # TODO order by deadline
    return data


@click.group()
def cli():
    """Releasewarrior: helping you keep track of releases in flight

    Each sub command takes a product and version

    versioning:\n
    \tBetas: must have a 'b' within string\n
    \tRelease Candidates: must have a 'rc' within string\n
    \tESRs: must have an 'esr' within string\n
    """
    pass


@cli.command()
@click.argument('product', type=click.Choice(['firefox', 'devedition', 'fennec', 'thunderbird']))
@click.argument('version')
@click.option('--date', help="date of planned GTB. format: YYYY-MM-DD")
def track(product, version, date, logger=LOGGER, config=CONFIG):
    """start tracking an upcoming release.
    """
    # set defaults to options
    date = date or arrow.now('US/Pacific').format("YYYY-MM-DD")
    branch = get_branch(version)

    release = Release(product=product, version=version, branch=branch, date=date)

    data_path, wiki_path = get_release_files(release, logger, config)

    commit_msg = "{} {} started tracking upcoming release.".format(product, version)

    # validate we can exec the command call
    validate_track(release, logger, config)

    # determine release data
    data = generate_tracking_data(release, logger, config)

    # track the release
    wiki = generate_wiki(data, release, logger, config)
    data_path = write_data(data_path, data, release, logger, config)
    wiki_path = write_wiki(wiki_path, wiki, release, logger, config)
    logger.info(data_path)
    logger.info(wiki_path)
    commit([data_path, wiki_path], commit_msg, logger, config)


@cli.command()
@click.argument('product', type=click.Choice(['firefox', 'devedition', 'fennec', 'thunderbird']))
@click.argument('version')
@click.option('--resolve', multiple=True)
def prereq(product, version, resolve, logger=LOGGER, config=CONFIG):
    """add or update a pre requisite (pre gtb) human task
    """
    branch = get_branch(version)

    release = Release(product=product, version=version, branch=branch, date=None)

    data_path, wiki_path = get_release_files(release, logger, config)

    resolve_msg = "Resolved {}".format(resolve) if resolve else ""
    commit_msg = "{} {} - updated prerequisites. {}".format(product, version, resolve_msg)

    # validate we can exec the command call
    validate_track(release, logger, config)

    # determine release data
    data = load_json(data_path)
    data = update_prereq_tasks(data, resolve)

    # update the release
    wiki = generate_wiki(data, release, logger, config)
    data_path = write_data(data_path, data, release, logger, config)
    wiki_path = write_wiki(wiki_path, wiki, release, logger, config)
    logger.info(data_path)
    logger.info(wiki_path)
    commit([data_path, wiki_path], commit_msg, logger, config)


def get_release_files(release, logging, config):
    upcoming_path = os.path.join(config['release_pipeline_repo'],
                                 config['releases']['upcoming'][release.product])
    inflight_path = os.path.join(config['release_pipeline_repo'],
                                 config['releases']['inflight'][release.product])
    data_file = "{}-{}-{}.json".format(release.product, release.branch, release.version)
    wiki_file = "{}-{}-{}.md".format(release.product, release.branch, release.version)
    release_path = upcoming_path
    if os.path.exists(os.path.join(inflight_path, data_file)):
        release_path = inflight_path
    return [
        os.path.join(release_path, data_file),
        os.path.join(release_path, wiki_file)
    ]



@cli.command()
@click.argument('product', type=click.Choice(['firefox', 'devedition', 'fennec', 'thunderbird']))
@click.argument('version')
@click.option('--graphid', multiple=True)
def newbuild(product, version, graphid, logger=LOGGER, config=CONFIG):
    """add or update a pre requisite (pre gtb) human task
    """
    branch = get_branch(version)

    release = Release(product=product, version=version, branch=branch, date=None)

    data_path, wiki_path = get_release_files(release, logger, config)

    graphid_msg = "Graphids: {}".format(graphid) if graphid else ""
    commit_msg = "{} {} - new buildnum started. ".format(product, version, graphid_msg)

    # validate we can exec the command call
    validate_track(release, logger, config)


    # determine release data
    data = load_json(data_path)

    is_first_gtb = "upcoming" in data_path
    if is_first_gtb:
        #   delete json and md files from upcoming dir, and set new dest paths to be inflight
        repo = Repo(config['release_pipeline_repo'])
        inflight_dir = os.path.join(config['release_pipeline_repo'],
                                    config['releases']['inflight'][release.product])
        moved_files = repo.index.move([data_path, wiki_path, inflight_dir])
        # set data and wiki paths to new dest (inflight) dir
        # moved_files is a list of tuples representing [files_moved][destination_location]
        data_path = os.path.join(config['release_pipeline_repo'], moved_files[0][1])
        wiki_path = os.path.join(config['release_pipeline_repo'], moved_files[1][1])
    else:
        #  kill latest buildnum add new buildnum based most recent buildnum
        logger.info("most recent buildnum has been aborted, starting a new buildnum")
        newbuild = deepcopy(data["inflight"][-1])
        # abort the now previous buildnum
        data["inflight"][-1]["aborted"] = True
        for task in newbuild["human_tasks"]:
            # reset all tasks to unresolved
            task["resolved"] = False
        # reset issues
        newbuild["issues"] = []
        # increment buildnum
        newbuild["buildnum"] = newbuild["buildnum"] + 1
        # add new buildnum based on previous to current release
        data["inflight"].append(newbuild)
    data["inflight"][-1]["graphids"] = [_id for _id in graphid]

    # update the release
    wiki = generate_wiki(data, release, logger, config)
    data_path = write_data(data_path, data, release, logger, config)
    wiki_path = write_wiki(wiki_path, wiki, release, logger, config)
    logger.info(data_path)
    logger.info(wiki_path)
    commit([data_path, wiki_path], commit_msg, logger, config)
