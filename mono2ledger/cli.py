import re
import sys


def _trim(string: str) -> str:
    return re.sub("( |\t){2,}", " ", string.replace("\n", " ").strip())


def in_yellow(string):
    return f"\033[33mWARNING:\033[0m {_trim(string)}"


def in_red(string):
    return f"\033[31mERROR:\033[0m {_trim(string)}"


def warn(message):
    print(in_yellow(message), file=sys.stderr)


def err(message):
    # NOTE 2023-07-08: See comment in periodexpr parser
    exit(in_red(message))


def info(message):
    print(message, file=sys.stderr)
