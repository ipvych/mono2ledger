import sys


def in_yellow(string):
    _str = string.replace("\n", " ")
    return rf"\033[33mWARNING:\033[0m {_str}"


def in_red(string):
    _str = string.replace("\n", " ")
    return rf"\033[31mERROR:\033[0m {_str}"


def warn(message):
    print(in_yellow(message), file=sys.stderr)


def err(message):
    # NOTE 2023-07-08: See comment in periodexpr parser
    exit(in_red(message))


def info(message):
    print(message, file=sys.stderr)
