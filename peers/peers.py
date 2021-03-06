#!/usr/bin/env python
# coding=utf-8

# file: peers.py
# vim:ts=8:sw=4:sts=4

"""The Peers agent-based model - © 2010-2011 of Giovanni Luca Ciampaglia."""

from __future__ import division
from argparse import ArgumentParser, FileType
import numpy as np
import sys
from time import time
from cStringIO import StringIO
from warnings import warn
from heapq import heappop, heappush
from collections import deque

from .rand import randwpmf
from .utils import ttysize, IncIDMixin, NNAction
from .cpeers import loop as c_loop

class User(IncIDMixin):
    ''' Class for user instances '''
    __slots__ = [
            'opinion',         # \in [0,1], named this way for historic reasons
            'edits',           # \ge 0, number of edits performed
            'successes',       # \ge 0 and \le edits, number of successful edits
            'daily_sessions',  # activation rate
            'hourly_edits',    # edits / hour
            'session_edits',   # average # edits per session
    ]
    def __init__(self, edits, successes, opinion, daily_sessions, hourly_edits,
            session_edits):
        self.edits = edits
        self.successes = successes
        self.opinion = opinion 
        self.daily_sessions = daily_sessions
        self.hourly_edits = hourly_edits
        self.session_edits = session_edits
    @property
    def ratio(self):
        return self.successes / self.edits

class Page(IncIDMixin):
    ''' Class for page instances '''
    __slots__ = [
            'opinion',  # see User
            'edits',    # see User
    ]
    def __init__(self, edits, opinion):
        self.opinion = opinion
        self.edits = edits

def loop(tstart, tstop, args, users, pages, output, prng=np.random):
    ''' continuous time simulation loop '''
    t = tstart # current time
    uR = args.daily_users
    pR = args.daily_pages
    p1 = args.p_stop_long
    p2 = args.p_stop_short
    num_events = 0
    pactiv = [u.daily_sessions for u in users]
    pstop = [u.ratio * p1 + (1 - u.ratio) * p2 for u in users] 
    ppage = [p.edits for p in pages] 
    editsqueue = []
    if len(users):
        aR = np.sum(pactiv)
        dR = np.sum(pstop)
    else:
        aR, dR = 0.0, 0.0
    while True:
        R = aR + dR + uR + pR
        T = (1 - np.log(prng.uniform())) / R # time to next event
        if t + T > tstop:
            break
        while len(editsqueue):
            tt, user = heappop(editsqueue)
            if tt < t + T:
                try:
                    user_idx = users.index(user)
                except ValueError:
                    continue # skip tasks of stopped users
                if len(pages):
                    page_idx = randwpmf(ppage, prng=prng)
                    page = pages[page_idx]
                    # will later re-update it 
                    dR -= (user.ratio * p1 + (1 - user.ratio) * p2)
                    user.edits += 1
                    page.edits += 1
                    if np.abs(user.opinion - page.opinion) < args.confidence:
                        user.successes += 1
                        user.opinion += args.speed * ( page.opinion - user.opinion )
                        page.opinion += args.speed * ( user.opinion - page.opinion )
                    elif prng.rand() < args.rollback_prob:
                        page.opinion += args.speed * ( user.opinion - page.opinion )
                    # re-compute the probability user stops and update global
                    # rate
                    users[user_idx] = user
                    ups = (user.ratio * p1 + (1 - user.ratio) * p2)
                    pstop[user_idx] = ups
                    dR += ups
                    pages[page_idx] = page
                    ppage[page_idx] += 1
                    if output:
                        print tt, user.id, page.id
                    num_events += 1
            else:
                heappush(editsqueue, (tt, user))
                break
        t = t + T
        ev = randwpmf([aR, dR, uR, pR], prng=prng)
        if ev == 0: # edit cascade
            user_idx = randwpmf(pactiv, prng=prng)
            user = users[user_idx]
            heappush(editsqueue, (t, user))
            num_edits = prng.poisson(user.session_edits)
            times = (1 - np.log(prng.rand(num_edits))) / user.hourly_edits
            times = t + (times / 24.0).cumsum()
            for tt in times:
                heappush(editsqueue, (tt, user))
        elif ev == 1: # user stops
            user_idx = randwpmf(pstop, prng=prng)
            user = users[user_idx]
            aR -= user.daily_sessions
            dR -= (user.ratio * p1 + (1 - user.ratio) * p2)
            del user
            del users[user_idx]
            del pstop[user_idx]
            del pactiv[user_idx]
        elif ev == 2: # new user
            o = prng.uniform()
            user = User(args.const_succ, args.const_succ, o, 
                    args.daily_sessions, args.hourly_edits,
                    args.session_edits)
            users.append(user)
            ups = (user.ratio * p1 + (1 - user.ratio) * p2)
            aR += user.daily_sessions
            dR += ups
            pstop.append(ups)
            pactiv.append(user.daily_sessions)
        else: # new page
            if len(users):
                user_idx = prng.randint(0, len(users))
                user = users[user_idx]
                page = Page(args.const_pop + 1, user.opinion)
                pages.append(page)
                ppage.append(page.edits)
        if args.info_file is not None:
            args.info_file.write('%g %g %g\n' % (t, len(users), len(pages)))
    return num_events

def simulate(args):
    '''
    Performs one simulation.

    Parameters
    ----------
    args - an Arguments instance

    Returns
    -------
    prng, users, pages
    '''
    prng = np.random.RandomState(args.seed)
    # users have a fixed activity rate and an initial number of ``successes''
    users = [ User(args.const_succ, args.const_succ, o, args.daily_sessions,\
            args.hourly_edits, args.session_edits) for o in 
            prng.random_sample(args.num_users) ]
    # pages have an initial value of popularity.
    pages = [ Page(args.const_pop,o) for o in prng.random_sample(args.num_pages) ]
    if args.transient:
        t_transient_start = time()
        if args.fast:
            n_transient = c_loop(0, args.transient, args, users, pages, False, prng)
        else:
            n_transient = loop(0, args.transient, args, users, pages, False, prng)
        t_transient_stop = time()
        t_transient = t_transient_stop - t_transient_start
        if args.verbosity > 0:
            print >> sys.stderr, 'Transient done in %.2gs (%g events/s)'\
                    % (t_transient, n_transient / t_transient)
    t_sim_start = time()
    if args.fast:
        n_sim = c_loop(args.transient, args.transient + args.time, args, users, 
                pages, True, prng)
    else:
        n_sim = loop(args.transient, args.transient + args.time, args, users, 
                pages, True, prng)
    t_sim_stop = time()
    t_sim = t_sim_stop - t_sim_start
    if args.verbosity > 0:
        print >> sys.stderr, 'Simulation done in %.2gs (%g events/s)'\
                % (t_sim, n_sim / t_sim)
    return prng, users, pages

class Arguments(object):
    """
    Class for checking parameter values and for printing simulation's results
    """
    def __init__(self, args):
        self.__dict__.update(args.__dict__)
        self._check()
        self.p_stop_long = self.long_life ** -1
        self.p_stop_short = self.short_life ** -1
    def _check(self): # raise exceptions
        if self.time < 0:
            raise ValueError('simulation duration cannot be negative: %d' %
                    self.time)
        if self.seed is not None and self.seed < 0:
            raise ValueError('seed cannot be negative: %d' % self.seed)
        if self.confidence < 0 or self.confidence > 1:
            raise ValueError('confidence must be in [0,1] (-c/--confidence)')
        if self.rollback_prob < 0 or self.rollback_prob > 1:
            raise ValueError('rollback_prob must be in [0,1] (--rollback-prob)')
        if self.speed < 0 or self.speed > 0.5:
            raise ValueError('speed must be in [0, 0.5] (--speed)')
    def _warn(self): # raise warnings
        if self.seed is None:
            warn('no seed was specified', category=UserWarning)
        if self.daily_sessions == 0:
            warn('turning off editing sessions', category=UserWarning)
        if self.hourly_edits == 0:
            warn('setting edits/hour to 0', category=UserWarning)
        if self.session_edits == 0:
            warn('setting average edits/session to 0', category=UserWarning)
        if self.daily_users == 0:
            warn('turning off new users arrival', category=UserWarning)
        if self.daily_pages == 0:
            warn('turning off page creation', category=UserWarning)
        if self.confidence == 0:
            warn('edits will always result in failure', category=UserWarning)
        if self.confidence == 1:
            warn('edits always result in success', category=UserWarning)
        if self.rollback_prob == 0:
            warn('no rollback edits', category=UserWarning)
        if self.rollback_prob == 1:
            warn('always do rollback edits', category=UserWarning)
        if self.speed == 0:
            warn('turning off opinion update', category=UserWarning)
    def __str__(self):
        h,w = ttysize() or (50, 85)
        sio = StringIO()
        print >> sio, '-'*w
        print >> sio, 'TIME.\tSimulation: %g (days).\tTransient: %g (days).'\
                % (self.time, self.transient)
        print >> sio, 'USERS.\tInitial: %d (users).\tIn-rate: %g (users/day).' % (
                self.num_users, self.daily_users)
        print >> sio, '\tLong life: %g (days)\tShort life: %g (days)'\
                % (self.long_life, self.short_life)
        print >> sio, 'EDITS.\tSessions/Day: %g.\tEdits/Hour: %g.'\
                '\tEdits/Session: %d.' % (self.daily_sessions,
                        self.hourly_edits, self.session_edits)
        print >> sio, 'PAGES.\tInitial: %d (pages).\tIn-rate: %g (pages/day).' % (
                self.num_pages, self.daily_pages)
        print >> sio, 'PAIRS.\tBase success: %g.\tBase popularity: %g.'\
                % (self.const_succ, self.const_pop)
        print >> sio, 'EDITS.\tConfidence: %g.\tSpeed: %g.\t\tRollback-prob.: %g.'\
                % (self.confidence, self.speed, self.rollback_prob)
        print >> sio, 'MISC.\tSeed: %s\t\tInfo file: %s.' % (self.seed,
                 self.info_file.name if self.info_file else 'None')
        try:
            import sys
            sys.stderr = sio
            self._warn()
        finally:
            sys.stderr = sys.__stderr__
        print >> sio, '-'*w,
        return sio.getvalue()

def make_parser(): 
    parser = ArgumentParser(description=__doc__, fromfile_prefix_chars='@')
    parser.add_argument('time', type=float, help='simulation duration, in days')
    parser.add_argument('seed', type=int, nargs='?', help='seed of the '
            'pseudo-random numbers generator', metavar='seed' )
    parser.add_argument('-T', '--transient', type=float, metavar='DAYS',
            help='transient duration (default: %(default)g)', default=0.0,
            action=NNAction)
    parser.add_argument('-a', '--daily-sessions', type=float, metavar='RATE',
            help='daily number of sessions', default=1.0,
            action=NNAction)
    parser.add_argument('-e', '--hourly-edits', type=float, metavar='EDITS',
            help='hourly number of edits', default=1.0, action=NNAction)
    parser.add_argument('-E', '--edits', type=int, metavar='EDITS',
            help='edits per session', default=1, action=NNAction,
            dest='session_edits')
    parser.add_argument('-L', '--long-life', type=float, metavar='DAYS',
            help='user long-term lifespan (default: %(default)g)',
            default=100.0, action=NNAction)
    parser.add_argument('-l', '--short-life', type=float, metavar='DAYS',
            help='user short-term lifespan (default: %(default)g)', 
            default=1.0/24.0, action=NNAction)
    parser.add_argument('-u', '--users', type=int, default=0, help='initial'
            ' number of users (default: %(default)d)', dest='num_users',
            metavar='NUM', action=NNAction)
    parser.add_argument('-p', '--pages', type=int, default=0, help='initial'
            ' number of pages (default: %(default)d)', dest='num_pages',
            metavar='NUM', action=NNAction)
    parser.add_argument('-U', '--daily-users', metavar='RATE', default=1.0, 
            type=np.double, help='daily rate of new users (default: '
            '%(default)g)', action=NNAction)
    parser.add_argument('-P', '--daily-pages', metavar='RATE', default=1.0, 
            type=np.double, help='daily rate of new pages (default: '
            '%(default)g)', action=NNAction)
    parser.add_argument('-c', '--confidence', type=np.double, default=.2,
            help='confidence parameter (default: %(default)g)')
    parser.add_argument('-s', '--speed', type=np.double, default=0.5,
            help='opinion averaging speed (default: %(default)g)')
    parser.add_argument('--const-succ', metavar='EDITS', type=float,
            default=1.0, help='base user successes (default: %(default)g)',
            action=NNAction)
    parser.add_argument('--const-pop', metavar='EDITS', type=float,
            default=1.0, help='base page popularity (default: %(default)g)',
            action=NNAction)
    parser.add_argument('-r', '--rollback-prob', metavar='PROB', type=np.double,
            default=0.5, help='roll-back probability (default: %(default)g)')
# misc
    parser.add_argument('-n', '--dry-run', action='store_true',
            help='do not simulate, just print parameters defaults')
    parser.add_argument('-D', '--debug', action='store_true', 
            help='raise Python exceptions to the console')
    parser.add_argument('-i', '--info', type=FileType('w'), help='write '
            'simulation information to %(metavar)s', metavar='FILE',
            dest='info_file')
    parser.add_argument('--fast', action='store_true', help='Use Cython '
            'implementation')
# profiling
    parser.add_argument('--profile', action='store_true', help='run profiling')
    parser.add_argument('--profile-file', metavar='FILE', default=None,
            help="store profiling information in file")
# verbosity
    parser.add_argument('--no-banner', action='store_const', const=1,
            dest='verbosity', help='do not print banner.')
    parser.add_argument('-q', '--quiet', action='store_const', const=0,
            dest='verbosity', help='do not print the banner')
    parser.set_defaults(verbosity=2)
    return parser

def main(args):
    '''
    Parameters
    ----------
    args - a Namespace parsed from parser generated with make_parser

    Example
    -------
    >>> from peers import make_parser, main
    >>> parser = make_parser()
    >>> ns = parser.parse('10 1'.split()) # will use defaults
    >>> main(ns)
    ...
    '''
    try:
        args = Arguments(args) # will check argument values here
        if args.verbosity > 1:
            print >> sys.stderr, args
        if not args.dry_run:
            if args.profile:
                import pstats, cProfile
                fn = args.profile_file or __file__ + ".prof"
                cProfile.runctx('simulate(args)', globals(), locals(), fn)
                stats = pstats.Stats(fn)
                stats.strip_dirs().sort_stats("time").print_stats()
            else:
                prng, users, pages = simulate(args)
    except:
        ty, val, tb = sys.exc_info()
        if args.debug:
            raise ty, val, tb
        else:
            name = ty.__name__
            print >> sys.stderr, '\n%s: %s\n' % (name, val)
    finally:
        if args.info_file:
            args.info_file.close()

if __name__ == '__main__':
    parser = make_parser()
    ns = parser.parse_args()
    main(ns)
