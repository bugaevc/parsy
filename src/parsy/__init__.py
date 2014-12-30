# -*- coding: utf-8 -*- #

import re
from .version import __version__
from functools import wraps
from collections import namedtuple

def line_info_at(stream, index):
    if index > len(stream): raise "invalid index"

    prefix = stream[0:index]
    line = prefix.count("\n")
    last_nl = prefix.rfind("\n")
    col = index - 1 - last_nl if last_nl >= 0 else index
    return (line, col)

class ParseError(RuntimeError):
    def __init__(self, expected, stream, index):
        self.expected = expected
        self.stream = stream
        self.index = index

    def line_info(self):
        return line_info_at(self.stream, self.index)

    def __str__(self):
        (line, col) = self.line_info()
        return 'parse error: expected {!s} at {!r}:{!r}'.format(self.expected, line, col)

class Parser(object):
    """
    A Parser is an object that wraps a function whose arguments are
    a string to be parsed and the index on which to begin parsing.
    The function returns a 3-tuple of (status, next_index, value),
    where the status is True if the parse was successful and False
    otherwise, the next_index is where to begin the next parse
    (or where to report a failure), and the value is the yielded value
    (or an error message).
    """

    def __init__(self, wrapped_fn):
        self.wrapped_fn = wrapped_fn

    def __call__(self, stream, index):
        return self.wrapped_fn(stream, index)

    def parse(self, string):
        """Parse a string and return the result or raise a ParseError."""
        (result, _) = (self << eof).parse_partial(string)
        return result

    def parse_partial(self, string):
        """
        Parse the longest possible prefix of a given string.
        Return a tuple of the result and the rest of the string,
        or raise a ParseError.
        """
        (status, index, value) = self(string, 0)

        if status:
            return (value, string[index:])
        else:
            raise ParseError(value, string, index)

    def bind(self, bind_fn):
        @Parser
        def bound_parser(stream, index):
            (success, new_index, value) = self(stream, index)

            if success:
                next_parser = bind_fn(value)
                return next_parser(stream, new_index)
            else:
                return (False, index, value)

        return bound_parser

    def map(self, map_fn):
        return self.bind(lambda res: success(map_fn(res)))

    def then(self, other):
        return self.bind(lambda _: other)

    def skip(self, other):
        return self.bind(lambda res: other.result(res))

    def result(self, res):
        return self >> success(res)

    def many(self):
        @Parser
        def many_parser(stream, index):
            aggregate = []
            next_index = index

            while True:
                (status, next_index, value) = self(stream, index)
                if status:
                    aggregate.append(value)
                    index = next_index
                else:
                    break

            return (True, index, aggregate)

        return many_parser

    def times(self, min, max=None):
        if max is None:
            max = min

        @Parser
        def times_parser(stream, index):
            aggregate = []
            next_index = index

            for times in range(0, min):
                (status, next_index, value) = self(stream, index)
                index = next_index
                if status:
                    aggregate.append(value)
                else:
                    return (False, index, value)

            for times in range(min, max):
                (status, next_index, value) = self(stream, index)
                if status:
                    index = next_index
                    aggregate.append(value)
                else:
                    break

            return (True, index, aggregate)

        return times_parser

    def at_most(self, n):
        return self.times(0, n)

    def at_least(self, n):
        return self.times(n) + self.many()

    def desc(self, description):
        return self | fail(description)

    def mark(self):
        @generate
        def marked():
            start = yield line_info
            body = yield self
            end = yield line_info
            return (start, body, end)

        return marked

    def __add__(self, other):
        return self.bind(lambda res: other.map(lambda res2: res+res2))

    def __mul__(self, other):
        if isinstance(other, range):
            return self.times(other.start, other.stop-1)
        return self.times(other)

    def __or__(self, other):
        if not isinstance(other, Parser):
            raise TypeError('{!r} is not a parser!'.format(other))

        @Parser
        def or_parser(stream, index):
            def failure(new_index, message):
                # we use the closured index here so it backtracks
                return other(stream, index)

            (status, next_index, value) = self(stream, index)
            if status:
                return (True, next_index, value)
            else:
                return other(stream, index)

        return or_parser

    # haskelley operators, for fun #

    # >>
    def __rshift__(self, other):
        return self.then(other)

    # <<
    def __lshift__(self, other):
        return self.skip(other)

# combinator syntax
def generate(fn):
    if isinstance(fn, str):
        return lambda f: generate(f).desc(fn)

    @wraps(fn)
    @Parser
    def generated(stream, index):
        iterator = fn()
        value = None
        try:
            while True:
                next_parser = iterator.send(value)
                (status, index, value) = next_parser(stream, index)
                if not status:
                    return (False, index, value)
        except StopIteration as result:
            returnVal = result.value
            if isinstance(returnVal, Parser):
                return returnVal(stream, index)

            return (True, index, returnVal)

    return generated.desc(fn.__name__)

@Parser
def index(stream, index):
    return (True, index, index)

@Parser
def line_info(stream, index):
    return (True, index, line_info_at(stream, index))

def success(val):
    return Parser(lambda _, index: (True, index, val))

def fail(message):
    return Parser(lambda _, index: (False, index, message))

def string(s):
    slen = len(s)

    @Parser
    def string_parser(stream, index):
        if stream[index:index+slen] == s:
            return (True, index+slen, s)
        else:
            return (False, index, s)

    string_parser.__name__ = 'string_parser<%s>' % s

    return string_parser

def regex(exp, flags=0):
    if isinstance(exp, str):
        exp = re.compile(exp, flags)

    @Parser
    def regex_parser(stream, index):
        match = exp.match(stream, index)
        if match:
            return (True, match.end(), match.group(0))
        else:
            return (False, index, exp.pattern)

    regex_parser.__name__ = 'regex_parser<%s>' % exp.pattern

    return regex_parser

whitespace = regex(r'\s+')

def item_matcher(fn, message=None):
    @Parser
    def matcher(stream, index):
        if index < len(stream):
            if fn(stream[index]):
                return (True, index+1, stream[index])
        return (False, index, message)
    return matcher

letter = item_matcher(str.isalpha, 'a letter')
digit = item_matcher(str.isdigit, 'a digit')

@Parser
def eof(stream, index):
    if index < len(stream):
        return (False, index, 'EOF')

    return (True, index, None)
