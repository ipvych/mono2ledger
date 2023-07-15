import re
import sys


def _trim(string: str) -> str:
    return re.sub("( |\t){2,}", " ", string.replace("\n", " ").strip())


def in_yellow(string):
    return f"\033[33mWARNING:\033[0m {_trim(string)}"


def in_red(string):
    return f"\033[31mERROR:\033[0m {_trim(string)}"


def warn(*strings):
    print(*map(in_yellow, strings), file=sys.stderr)


def err(*strings):
    # NOTE 2023-07-08: See comment in periodexpr parser
    exit(in_red(" ".join(strings)))


def info(*strings):
    print(*strings, file=sys.stderr)
