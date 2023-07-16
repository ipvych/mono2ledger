import re


def _trim(string: str) -> str:
    return re.sub("( |\t){2,}", " ", string.replace("\n", " ").strip())


def in_red(string):
    return f"\033[31mERROR:\033[0m {_trim(string)}"


def err(*strings):
    # NOTE 2023-07-08: See comment in periodexpr parser
    exit(in_red(" ".join(strings)))
