#!/usr/bin/env python3
"""Runs the Ethos tests (tests/test_*.py) on simulators and / or a radio.

  python tests/run_tests.py --simulator "d:/Projects/ethos/build/*/simulator/*.js" [TEST...]
      Each WASM simulator (any radio, release or debug, any country protocol: the
      quotes keep the wildcards for Python), started on a fresh storage.

  python tests/run_tests.py --simulator X20RS [--simulator "X18*"] [--release 26.2.0-RC10] [TEST...]
      The simulators of a release on GitHub (the latest one by default, release
      candidates included), downloaded once in a cache by run_wasm.js (simulators.js,
      shared with the MCP server): X20RS (its FCC simulator), X20RS-FLEX, X18-LBT,
      X20* (the FCC ones), X20RS-* (all its countries). A --simulator with a slash or a backslash, or ending with .js, is a file.

  python tests/run_tests.py --radio [usb|COMx] [TEST...]
      A radio on a serial port (USB mode Serial): accept the connection on its screen.
      usb (or nothing): the first serial port with the Ethos USB id.

  python tests/run_tests.py --port N [TEST...]
      A radio_remote.py or run_wasm.js --serve already running.

TEST: a test module name (xact_phyid or test_xact_phyid), all of them by default.
Options of the tests: --option name=value (see each test).

A test module has one or more classes derived from ethos_test.EthosBaseTest (see
its docstring): steps() gives its steps, is_available() whether it runs on a target.

The screenshots and the server logs are written to the --out folder.
"""

import argparse
import glob
import importlib
import inspect
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ethos_remote import Target, TestFailure, TestSkipped  # noqa: E402
from ethos_test import EthosBaseTest  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def find_tests(names):
    available = sorted(f[5:-3] for f in os.listdir(TESTS_DIR) if f.startswith('test_') and f.endswith('.py'))
    if not names:
        return available
    selected = []
    for name in names:
        name = os.path.splitext(os.path.basename(name))[0]
        name = name[5:] if name.startswith('test_') else name
        if name not in available:
            sys.exit(f'No test {name}, the tests: {", ".join(available)}')
        selected.append(name)
    return selected


def test_classes(name):
    """The (test name, class) of a test module: name, or name.Class when it has several."""
    module = importlib.import_module(f'test_{name}')
    classes = [c for _, c in inspect.getmembers(module, inspect.isclass)
               if issubclass(c, EthosBaseTest) and c.__module__ == module.__name__]
    if not classes:
        sys.exit(f'test_{name}.py has no EthosBaseTest class')
    classes.sort(key=lambda c: inspect.getsourcelines(c)[1])
    return [(name if len(classes) == 1 else f'{name}.{c.__name__}', c) for c in classes]


def step_title(step):
    """A step: a (title, function) tuple, or a method titled by its docstring."""
    if isinstance(step, tuple):
        return step
    doc = (step.__doc__ or '').strip()
    return (doc.splitlines()[0] if doc else step.__name__), step


def run_test(test, no_skip):
    """PASSED, FAILED or SKIPPED, and the reason of a failure or a skip. teardown() runs once
    the test started, even when a step failed: a failure of it fails the test."""
    reason = test.skip_reason()
    if reason and not no_skip:
        return 'SKIPPED', reason
    try:
        result = run_steps(test)
    finally:
        if type(test).teardown is not EthosBaseTest.teardown:
            print(f'    teardown: {step_title(test.teardown)[0]}', flush=True)
        try:
            test.teardown()
            teardown_error = None
        except (TestFailure, TestSkipped) as e:
            teardown_error = str(e)
            print(f'      teardown failed: {e}', flush=True)
    if teardown_error and result[0] != 'FAILED':
        return 'FAILED', f'teardown: {teardown_error}'
    return result


def run_steps(test):
    try:
        if type(test).setup is not EthosBaseTest.setup:
            print(f'    setup: {step_title(test.setup)[0]}', flush=True)
            test.setup()
        for number, step in enumerate(test.steps(), 1):
            title, function = step_title(step)
            print(f'    {number}) {title}', flush=True)
            function()
        return 'PASSED', None
    except TestSkipped as e:
        return 'SKIPPED', str(e)
    except TestFailure as e:
        try:
            for screen in test.save_failure_screens():
                print(f'      screen: {screen}', flush=True)
        except (TestFailure, OSError, ImportError) as error:
            print(f'      no screen: {error}', flush=True)
        return 'FAILED', str(e)


def run_target(target, tests, args):
    out = os.path.join(os.path.abspath(args.out), target.name)
    os.makedirs(out, exist_ok=True)
    target.log_file = os.path.join(out, 'server.log')
    results = []
    try:
        remote = target.start(out)
        info = remote.information()
        if not (info.get('displays') or {}).get('MAIN'):
            remote.screenshot('start')  # the radio gives the display size once it was captured
            info = remote.information()
        print(f'== {target.describe(info)}', flush=True)
        for name, cls in tests:
            print(f'  {name}: {cls.TITLE}', flush=True)
            remote.shots_dir = os.path.join(out, name)
            remote.shot_index = 0
            started = time.time()
            result, reason = run_test(cls(target, remote, info, args.options), args.no_skip)
            print(f'  {name}: {result}' + (f' ({reason})' if reason else '') + f' [{time.time() - started:.0f} s]',
                  flush=True)
            results.append((target.name, name, result, reason))
    except TestFailure as e:
        print(f'== {target.name}: {e}', flush=True)
        results.append((target.name, '-', 'FAILED', str(e)))
    finally:
        target.stop()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('tests', nargs='*', help='the tests to run, all of them by default')
    parser.add_argument('--simulator', action='append', default=[],
                        help='a WASM simulator: a .js (wildcards allowed), or a radio of the release (X20RS, '
                             'X20RS_FLEX, X20*...), several times allowed')
    parser.add_argument('--release', default='latest',
                        help='the release of the simulators given by radio: a tag (26.2.0-RC10...) or latest '
                             '(default, release candidates included)')
    parser.add_argument('--cache', help='folder of the simulators downloaded (default: the one of the MCP server, '
                                        'ETHOS_CACHE_DIR or the user cache folder)')
    parser.add_argument('--root-directory', help='simulator storage to use instead of a fresh one (kept)')
    parser.add_argument('--radio', nargs='?', const='usb', metavar='usb|COM',
                        help='a radio on a serial port, usb (default): the first port with the Ethos USB id')
    parser.add_argument('--accept-timeout', type=float, default=120, help='seconds to accept the connection on the radio')
    parser.add_argument('--port', type=int, help='a command server already running')
    parser.add_argument('--option', action='append', default=[], metavar='NAME=VALUE', help='test option')
    parser.add_argument('--no-skip', action='store_true', help='run the tests even on the targets they skip')
    parser.add_argument('--out', default='test-results', help='screenshots and logs folder (default test-results)')
    args = parser.parse_args()
    args.options = dict(o.split('=', 1) if '=' in o else (o, '1') for o in args.option)

    targets = []
    if args.cache:
        os.environ['ETHOS_CACHE_DIR'] = os.path.abspath(args.cache)  # for run_wasm.js
    for pattern in args.simulator:
        if re.search(r'[/\\]|\.js$', pattern, re.IGNORECASE):
            paths = sorted(glob.glob(pattern)) or sys.exit(f'No simulator {pattern}')
            targets += [Target.simulator(p, args.root_directory) for p in paths]
            continue
        try:
            release, radios = Target.list_release_simulators(pattern, args.release)
        except TestFailure as e:
            sys.exit(str(e))
        targets += [Target.release_simulator(radio, release, args.root_directory) for radio in radios]
    if args.radio is not None:
        com = None if args.radio.lower() == 'usb' else args.radio
        targets.append(Target.radio(com, args.accept_timeout))
    if args.port:
        targets.append(Target.server(args.port))
    if not targets:
        parser.error('no target: --simulator, --radio or --port')

    tests = [test for name in find_tests(args.tests) for test in test_classes(name)]
    results = []
    for target in targets:
        results += run_target(target, tests, args)

    print('\nSummary')
    for target_name, name, result, reason in results:
        print(f'  {result:7}  {target_name:20} {name}' + (f'  ({reason})' if reason else ''))
    failed = sum(1 for r in results if r[2] == 'FAILED')
    print(f'{len(results)} tests: {sum(1 for r in results if r[2] == "PASSED")} passed, {failed} failed, '
          f'{sum(1 for r in results if r[2] == "SKIPPED")} skipped')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
