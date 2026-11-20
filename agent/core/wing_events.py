"""W.I.N.G. event (コミュ) choice: pure, deterministic, no input, no screen reading.

First TE run: "always pick the centre option" is unreliable because option meaning depends
on context, and accepting a 約束 (e.g. 「二週間後ボーカルレッスン」) raises trust and later tension. The rule kept
deliberately simple:

1. an option that accepts a promise -> take it (``accept_promises``, default true);
2. the event is in the user's ``choice_table`` -> that index;
3. otherwise the configured ``default_index`` (``manual.event_default_choice``, default 0 = top option).

The game's own countdown timeout is never used as a strategy: the executor must click a choice before it expires.
An index that does not exist on screen stops (no guess). Promise options are flagged by the reader from the option
text; this module does not parse Japanese.
"""
from __future__ import annotations

import dataclasses
from typing import Mapping, Sequence


@dataclasses.dataclass(frozen=True)
class EventOption:
    text: str
    promise: bool = False          # accepting this option creates a 約束 (reader-flagged)


@dataclasses.dataclass(frozen=True)
class EventChoice:
    index: int | None              # None = stop, do not click
    rule: str
    reasons: tuple = ()


def choose_event_option(options: Sequence[EventOption], *, event_key: str | None = None,
                        choice_table: Mapping[str, int] | None = None, default_index: int = 0,
                        accept_promises: bool = True) -> EventChoice:
    if not options:
        return EventChoice(None, "no_options")
    if accept_promises:
        promises = [i for i, o in enumerate(options) if o.promise]
        if len(promises) == 1:
            return EventChoice(promises[0], "accept_promise", (options[promises[0]].text,))
        if len(promises) > 1:
            return EventChoice(None, "ambiguous_promise_options", tuple(str(i) for i in promises))
    if event_key is not None and choice_table and event_key in choice_table:
        index, rule = choice_table[event_key], "choice_table"
    else:
        index, rule = default_index, "config_default"
    if type(index) is not int or not 0 <= index < len(options):
        return EventChoice(None, f"{rule}_index_out_of_range", (str(index), str(len(options))))
    return EventChoice(index, rule, (options[index].text,))
