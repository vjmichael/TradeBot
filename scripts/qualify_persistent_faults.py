"""Qualification-only controlled-book faults in an offline systemd guest.

Never used by the production ExecStart. The root-owned temporary drop-in selects
one fixed test scenario; no production fault selector or execution endpoint.
"""
import os
import sys
import time
from contextlib import contextmanager
from qualify_persistent_systemd_fixture import factory as baseline, admit


@contextmanager
def factory(i,p,c,w,mode):
    from freqtrade.persistence import Trade
    with baseline(i,p,c,w) as d:
        entry=d.entry
        if mode=='partial':
            apply=d._apply_frozen
            def freeze_cut(row,raw):
                # The frozen receipt already committed. Only fault the process
                # that observed the actual pending order, not recovery processes.
                if armed:
                    print('CUT_FROZEN_PARTIAL',flush=True)
                    time.sleep(90)
                    raise AssertionError('expected supervised interruption')
                return apply(row,raw)
            armed=False
            d._apply_frozen=freeze_cut
            def partial(row,guard):
                nonlocal armed
                d.bot.exchange.mid=101.
                entry(row,guard)
                t=Trade.get_trades_proxy(is_open=True)[0];o=t.open_orders[0]
                raw=d.bot.exchange._dry_run_open_orders[o.order_id]
                # Pinned simulator is all-or-none: controlled cumulative partial
                # observation, not a venue fill. Actual freeze/ORM/exit follow.
                raw.update(filled=.2,remaining=raw['amount']-.2,cost=20.,average=100.,fee={'currency':'USD','cost':.10,'rate':.005})
                armed=True
                d.cancel_before_poll(row)
                raise AssertionError('expected interruption')
            d.entry=partial
        elif mode=='watchdog':
            def blocked(row,guard):
                entry(row,guard)
                print('CUT_PERSISTED_BUY',flush=True)
                time.sleep(90)
                raise AssertionError('expected watchdog interruption')
            d.entry=blocked
        else:raise ValueError('qualification mode')
        yield d


if __name__=='__main__':
    from reserve_persistent_worker import run
    assert len(sys.argv)==2 and sys.argv[1] in ('partial','watchdog')
    mode=sys.argv[1]
    run(factory=lambda i,p,c,w:factory(i,p,c,w,mode))
