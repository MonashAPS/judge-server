import argparse
import glob
import logging
import os
import ssl
from fnmatch import fnmatch
from operator import itemgetter
from typing import Dict, Iterable, List, Optional, Set, Tuple

import yaml

from dmoj.config import ConfigNode
from dmoj.utils import pyyaml_patch  # noqa: F401, imported for side effect
from dmoj.utils.ansi import print_ansi
from dmoj.utils.unicode import utf8text

problem_globs: List[str] = []
problem_watches: List[str] = []
env: ConfigNode = ConfigNode(
    defaults={
        'selftest_time_limit': 10,  # 10 seconds
        'selftest_memory_limit': 131072,  # 128mb of RAM
        'generator_compiler_time_limit': 30,  # 30 seconds
        'generator_time_limit': 20,  # 20 seconds
        'generator_memory_limit': 524288,  # 512mb of RAM
        'validator_compiler_time_limit': 30,  # 30 seconds
        'validator_time_limit': 20,  # 20 seconds
        'validator_memory_limit': 524288,  # 512mb of RAM
        'compiler_time_limit': 60,  # Kill compiler after 60 seconds
        'compiler_size_limit': 131072,  # Maximum allowable compiled file size, 128mb
        'compiler_output_character_limit': 65536,  # Number of characters allowed in compile output
        'compiled_binary_cache_dir': None,  # Location to store cached binaries, defaults to tempdir
        'compiled_binary_cache_size': 100,  # Maximum number of executables to cache (LRU order)
        'runtime': {},
        # Map of executor: fs_config, used to configure
        # the filesystem sandbox on a per-machine basis, without having to hack
        # executor source.
        # fs_config is a list of dictionaries. Each dictionary should contain one key/value pair.
        # Three keys are possible:
        # `exact_file`, to allow a specific file
        # `exact_dir`, to allow listing files in a directory
        # `recursive_dir`, to allow everything under and including a directory
        # Example YAML:
        # extra_fs:
        #   PERL:
        #   - exact_file: /dev/dtrace/helper
        #   - exact_dir: /some/exact/directory
        #   - recursive_dir: /some/directory/and/all/children
        'extra_fs': {},
        # List of judge URLs to ping on problem data updates (the URLs are expected
        # to host judges running with --api-host and --api-port)
        'update_pings': [],
        # Directory to use as temporary submission storage, system default
        # (e.g. /tmp) if left blank.
        'tempdir': None,
        # CPU affinity (as a list of 0-indexed CPU IDs) to run submissions on
        'submission_cpu_affinity': None,
    },
    dynamic=False,
)
_root: str = os.path.dirname(__file__)

log_file = server_host = server_port = no_ansi = skip_self_test = no_watchdog = problem_regex = case_regex = None
cli_history_file = cert_store = api_listen = None
secure: bool = False
no_cert_check: bool = False
log_level = logging.DEBUG

startup_warnings: List[str] = []
cli_command: List[str] = []

only_executors: Set[str] = set()
exclude_executors: Set[str] = set()


def load_env(cli: bool = False, testsuite: bool = False) -> None:  # pragma: no cover
    global problem_globs, only_executors, exclude_executors, log_file, server_host, server_port, no_ansi, no_ansi_emu, skip_self_test, env, startup_warnings, no_watchdog, problem_regex, case_regex, api_listen, secure, no_cert_check, cert_store, problem_watches, cli_history_file, cli_command, log_level

    if cli:
        description = 'Starts a shell for interfacing with a local judge instance.'
    else:
        description = 'Spawns a judge for a submission server.'

    parser = argparse.ArgumentParser(description=description)
    if not cli:
        parser.add_argument('server_host', help='host to connect for the server')
        parser.add_argument('judge_name', nargs='?', help='judge name (overrides configuration)')
        parser.add_argument('judge_key', nargs='?', help='judge key (overrides configuration)')
        parser.add_argument('-p', '--server-port', type=int, default=9999, help='port to connect for the server')
    elif not testsuite:
        parser.add_argument('command', nargs='*', help='invoke CLI command without spawning shell')
        parser.add_argument(
            '--history',
            type=str,
            default='~/.dmoj_history',
            help='file to load and save command history (default: ~/.dmoj_history)',
        )

    parser.add_argument(
        '-c',
        '--config',
        type=str,
        default='~/.dmojrc',
        help='file to load judge configurations from (default: ~/.dmojrc)',
    )

    parser.add_argument(
        '-d',
        '--debug',
        action='store_const',
        const=logging.DEBUG,
        default=logging.INFO,
        dest='log_level',
    )

    if not cli:
        parser.add_argument('-l', '--log-file', help='log file to use')
        parser.add_argument('--no-watchdog', action='store_true', help='disable use of watchdog on problem directories')
        parser.add_argument(
            '-a',
            '--api-port',
            type=int,
            default=None,
            help='port to listen for the judge API (do not expose to public, '
            'security is left as an exercise for the reverse proxy)',
        )
        parser.add_argument('-A', '--api-host', default='127.0.0.1', help='IPv4 address to listen for judge API')

        parser.add_argument('-s', '--secure', action='store_true', help='connect to server via TLS')
        parser.add_argument('-k', '--no-certificate-check', action='store_true', help='do not check TLS certificate')
        parser.add_argument(
            '-T', '--trusted-certificates', default=None, help='use trusted certificate file instead of system'
        )

    _group = parser.add_mutually_exclusive_group()
    _group.add_argument('-e', '--only-executors', help='only listed executors will be loaded (comma-separated)')
    _group.add_argument('-x', '--exclude-executors', help='prevent listed executors from loading (comma-separated)')

    parser.add_argument('--no-ansi', action='store_true', help='disable ANSI output')

    parser.add_argument('--skip-self-test', action='store_true', help='skip executor self-tests')

    if testsuite:
        parser.add_argument('tests_dir', help='directory where tests are stored')
        parser.add_argument('problem_regex', help='when specified, only matched problems will be tested', nargs='?')
        parser.add_argument('case_regex', help='when specified, only matched cases will be tested', nargs='?')

    args = parser.parse_args()

    server_host = getattr(args, 'server_host', None)
    server_port = getattr(args, 'server_port', None)
    cli_command = getattr(args, 'command', [])
    cli_history_file = getattr(args, 'history', None)
    if cli_history_file:
        cli_history_file = os.path.expanduser(cli_history_file)

    no_ansi = args.no_ansi
    skip_self_test = args.skip_self_test
    no_watchdog = True if cli else args.no_watchdog
    log_level = args.log_level
    if not cli:
        api_listen = (args.api_host, args.api_port) if args.api_port else None

        if ssl:
            secure = args.secure
            no_cert_check = args.no_certificate_check
            cert_store = args.trusted_certificates

    log_file = getattr(args, 'log_file', None)
    only_executors |= args.only_executors and set(args.only_executors.split(',')) or set()
    exclude_executors |= args.exclude_executors and set(args.exclude_executors.split(',')) or set()

    is_docker = bool(os.getenv('DMOJ_IN_DOCKER'))
    if is_docker:
        if not cli:
            api_listen = api_listen or ('0.0.0.0', 9998)

        with open('/judge-runtime-paths.yml', 'rb') as runtimes_file:
            env.update(yaml.safe_load(runtimes_file))

        problem_globs = ['/problems/**/']

    model_file = os.path.expanduser(args.config)
    try:
        with open(model_file) as init_file:
            env.update(yaml.safe_load(init_file))
    except IOError:
        if not is_docker:
            raise

    if getattr(args, 'judge_name', None):
        env['id'] = args.judge_name
    elif 'DMOJ_JUDGE_NAME' in os.environ:
        env['id'] = os.environ['DMOJ_JUDGE_NAME']

    if getattr(args, 'judge_key', None):
        env['key'] = args.judge_key
    elif 'DMOJ_JUDGE_KEY' in os.environ:
        env['key'] = os.environ['DMOJ_JUDGE_KEY']

    if env.problem_storage_globs:
        problem_globs = env.problem_storage_globs
        # Populate cache and send warnings
        get_problem_roots(warnings=True)

        problem_watches = problem_globs

    if problem_globs is None and not testsuite:
        raise SystemExit(f'`problem_storage_globs` not specified in "{model_file}"; no problems available to grade')

    if testsuite:
        if not os.path.isdir(args.tests_dir):
            raise SystemExit('Invalid tests directory')
        problem_globs = [os.path.join(args.tests_dir, '*')]
        clear_problem_dirs_cache()

        import re

        if args.problem_regex:
            try:
                problem_regex = re.compile(args.problem_regex)
            except re.error:
                raise SystemExit('Invalid problem regex')
        if args.case_regex:
            try:
                case_regex = re.compile(args.case_regex)
            except re.error:
                raise SystemExit('Invalid case regex')


_problem_root_cache: Dict[str, str] = {}


def get_problem_root(problem_id) -> Optional[str]:
    cached_root = _problem_root_cache.get(problem_id)
    if cached_root is None or not os.path.isfile(os.path.join(cached_root, 'init.yml')):
        for root_dir in get_problem_roots():
            problem_root_dir = os.path.join(root_dir, *problem_id.split("."))
            problem_config = os.path.join(problem_root_dir, 'init.yml')
            if os.path.isfile(problem_config):
                if problem_globs and not any(
                    fnmatch(problem_config, os.path.join(problem_glob, 'init.yml')) for problem_glob in problem_globs
                ):
                    continue
                _problem_root_cache[problem_id] = problem_root_dir
                break
        else:
            return None

    return _problem_root_cache[problem_id]


_problem_dirs_cache: Optional[List[str]] = None


def get_problem_roots(warnings: bool = False) -> List[str]:
    global _problem_dirs_cache

    if _problem_dirs_cache is not None:
        return _problem_dirs_cache

    dirs = []
    dirs_set = set()
    for dir_glob in problem_globs:
        config_glob = os.path.join(dir_glob, 'init.yml')
        root_dirs = {os.path.dirname(os.path.dirname(x)) for x in glob.iglob(config_glob, recursive=True)}
        for root_dir in root_dirs:
            if root_dir not in dirs_set:
                dirs.append(root_dir)
                dirs_set.add(root_dir)

    if warnings:
        cleaned_dirs = []
        for dir in dirs:
            if not os.path.isdir(dir):
                startup_warnings.append('cannot access problem directory %s (does it exist?)' % dir)
                continue
            cleaned_dirs.append(dir)
    else:
        cleaned_dirs = dirs
    _problem_dirs_cache = cleaned_dirs
    return cleaned_dirs


def clear_problem_dirs_cache() -> None:
    global _problem_dirs_cache
    _problem_dirs_cache = None


def get_problem_watches() -> List[str]:
    return problem_watches


def get_supported_problems_and_mtimes(warnings: bool = True) -> List[Tuple[str, float]]:
    """
    Fetches a list of all problems supported by this judge and their mtimes.
    :return:
        A list of all problems in tuple format: (problem id, mtime)
    """
    problems = []
    problem_dirs: Dict[str, str] = {}
    for dir_glob in problem_globs:
        for problem_config in glob.iglob(os.path.join(dir_glob, 'init.yml'), recursive=True):
            if os.access(problem_config, os.R_OK):
                problem_dir = os.path.dirname(problem_config)
                problem = utf8text(os.path.basename(problem_dir))

                # Temp print
                print_ansi(
                    f'#ansi[LOG: problem {problem} found at {problem_dir}](green)'
                )

                if problem in problem_dirs:
                    if warnings:
                        print_ansi(
                            f'#ansi[Warning: duplicate problem {problem} found at {problem_dir},'
                            f' ignoring in favour of {problem_dirs[problem]}](yellow)'
                        )
                else:
                    problem_dirs[problem] = problem_dir
                    problems.append((problem, os.path.getmtime(problem_dir)))
    return problems


def get_supported_problems(warnings: bool = True) -> Iterable[str]:
    return map(itemgetter(0), get_supported_problems_and_mtimes(warnings=warnings))


def get_runtime_versions():
    from dmoj.executors import executors

    return {name: clazz.Executor.get_runtime_versions() for name, clazz in executors.items()}
