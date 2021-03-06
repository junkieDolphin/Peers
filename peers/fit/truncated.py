''' Fits mixture of truncated univariate normals to data using EM '''

from argparse import ArgumentParser
import numpy as np
from scipy.stats import kstest, norm
from scipy.stats.distributions import _norm_pdf 
from scipy.special import ndtr as norm_cdf 
from scipy.cluster.vq import kmeans2
import sys
import os.path 
from warnings import warn
import matplotlib.pyplot as pp
from matplotlib import cm
from datetime import timedelta
from multiprocessing import Pool, Array
import signal

from ..utils import sanetext, fmt
from ..rand import randwpmf
from ..graphics import mixturehist
from .ctruncated import EM as cEM

# On Mac OS X inf**2 raises OverflowError.  This is normal. See here:
# http://bugs.python.org/issue3222

if sys.platform == 'darwin':
    def norm_pdf(x):
        return np.where(np.isinf(x), 0, _norm_pdf(x))
else:
    norm_pdf = _norm_pdf
 
def tnorm_pdf(x, mu, sigma, bound):
    ''' truncated normal density function '''
    x = np.asarray(x)
    x = (x - mu) / sigma
    u = (bound[1] - mu) / sigma
    l = (bound[0] - mu) / sigma
    c = norm_cdf(u) - norm_cdf(l)
    d = norm_pdf(x) / (c * sigma)
    return np.where((x >= l) & (x <= u), d, 0.)

def tnorm_cdf(x, mu, sigma, bound):
    ''' truncated normal distribution function '''
    x = np.asarray(x)
    x = (x - mu) / sigma
    u = (bound[1] - mu) / sigma
    l = (bound[0] - mu) / sigma
    c = norm_cdf(u) - norm_cdf(l)
    p = (norm_cdf(x) - norm_cdf(l)) / c
    p[x < l] = 0
    p[x > u] = 1
    return p
#    return np.where((x >= l) & (x <= u), p, 0.)

def _loglike(data, weights, mu, sigma, bound):
    n = len(data)
    k = len(weights)
    tmp = np.zeros((k, n))
    for i in xrange(k):
        tmp[i] = weights[i] * tnorm_pdf(data, mu[i], sigma[i], bound) 
    return np.sum(np.log(np.sum(tmp,axis=0)))

def _responsibilities(data, weights, mu, sigma, bound):
    ''' the E-step of the algorithm '''
    n = len(data)
    k = len(weights)
    g = np.zeros((k, n))
    for i in xrange(k):
        g[i] = tnorm_pdf(data,mu[i],sigma[i],bound) * weights[i]
    g = g.T / g.sum(axis=0)[:,np.newaxis]
    return g

def _tmeancost(mu, sigma, bound):
    ''' additive constant for the mean of the truncated normal '''
    l, u = bound
    l = (l - mu) / sigma
    u = (u - mu) / sigma
    n = norm_pdf(u) - norm_pdf(l)
    d = norm_cdf(u) - norm_cdf(l)
    c = sigma * n / d
    return c

def _tvarcost(mu, sigma, bound):
    ''' multiplicative factor for the variance of the truncated normal '''
    l,u = bound
    l = (l - mu) / sigma
    u = (u - mu) / sigma
    # as x --> +/- Inf, x * f(x) --> 0 for the gaussian density f, but 0. *
    # Inf would normally give NaN
    n1_1 = 0. if np.isinf(u) else u * norm_pdf(u) 
    n1_2 = 0. if np.isinf(l) else l * norm_pdf(l) 
    n1 = n1_1 - n1_2
    n2 = norm_pdf(u) - norm_pdf(l)
    d = norm_cdf(u) - norm_cdf(l)
    c = 1 + n1 / d - (n2 /d )**2 
    assert c > 0, "c = %g " % c
    return c

def _maximize(data, mu, sigma, bound, gamma):
    ''' the M-step of the algorithm. Moments estimates are for the non-truncated
    normal. '''
    n = len(data)
    k = len(mu)
    mu1 = np.zeros((k,))
    sigma1 = np.zeros((k,))
    w1 = gamma.sum(axis=0) / float(n)
    for i in xrange(k):
        muc = _tmeancost(mu[i],sigma[i],bound)
        varc = _tvarcost(mu[i],sigma[i],bound)
        mu1[i] = np.sum(data * gamma[:,i]) / gamma[:,i].sum() 
        sigma1[i] = np.sqrt( np.sum((data - mu1[i])**2 * gamma[:,i])\
                / gamma[:,i].sum())  
        mu1[i] -= muc
        sigma1[i] /= np.sqrt(varc)
    return w1, mu1, sigma1

def _init_EM(data, k, prng=np.random):
    ''' initializes with hard assignments to clusters using kmeans '''
    # ensurers deterministic result of kmeans2
    seed = prng.randint(0, sys.maxint)
    np.random.seed(seed)
    flag = True
    n = float(len(data))
    while flag:
        mu, assign = kmeans2(data, k, iter=5, minit='random')
        sigma = []
        weights = []
        for i in xrange(k):
            idx = (assign == i)
            sigma.append(np.std(data[idx], ddof=1))
            weights.append(np.sum(idx) / n)
        sigma = np.asarray(sigma)
        weights = np.asarray(weights)
        flag = True - np.all(weights > 0)
    return weights, mu, sigma

def EM(data, k, bounds=None, n_iter=100, thresh=1e-2, verbose=False, 
        prng=np.random):
    '''
    Fit a truncated GMM to data using the EM algorithm. 
    
    Parameters
    ----------
    data    - array (will be truncated within bounds)
    k       - number of components to fit
    bounds  - default: (data.min(), data.max()) 
    n_iter  - maximum number of iterations
    thresh  - stop iteration when marginal increment in loglike is below thresh
    verbose - if True, print log-likelihood and prior probabilities
    prng    - instance of numpy.random.RandomState
    
    Returns
    -------
    weights, mu, sigma - GMM parameters
    loglike            - the sequence of log-likelihood values
    flag               - True if convergence happened within maxiter
    ''' 
    data = np.ravel(data)
    if bounds is not None:
        l, u = bounds
        data = data[(data >= l) & (data <= u)]
    else:
        bounds = (np.min(data), np.max(data))
    weights, mu, sigma = _init_EM(data, k, prng)
    loglike = np.zeros((n_iter,))
    loglike[0] = _loglike(data, weights, mu, sigma, bounds)
    if verbose:
        print "0) LogLike = %g, Priors = %s" % (loglike[0], weights)
    for i in xrange(1, n_iter):
        gamma = _responsibilities(data, weights, mu, sigma, bounds)
        weights, mu, sigma = _maximize(data, mu, sigma, bounds, gamma)
        loglike[i] = _loglike(data, weights, mu, sigma, bounds) 
        if verbose:
            print "%d) LogLike = %g, Priors = %s" % (i, loglike[i], weights)
        flag = np.abs(loglike[i - 1] - loglike[i]) < thresh
        if flag:
            break
    return (weights, mu, sigma, np.trim_zeros(loglike), flag)

class TGMM(object):
    ''' 
    truncated gaussian mixture model. Supports univariate distributions only. 
    '''
    def __init__(self, components, bounds=None):
        '''
        if bounds are not specifiend, they will be evaluated from data
        '''
        self.components = components
        self.bounds = bounds
    def _get_bounds(self):
        return self._bounds
    def _set_bounds(self, bounds):
        if bounds is None:
            self._bounds = bounds
        else:
            if len(bounds) != 2:
                raise ValueError('bounds must have length 2')
            if bounds[0] > bounds[1]:
                raise ValueError('bounds (a, b) must be a <= b')
            self._bounds = tuple(bounds)
    def _get_weights(self):
        return self._weights
    def _set_weights(self, weights):
        if len(weights) != self.components:
            raise ValueError('weights must have length equal to number of ' 
                    'components')
        weights = np.asarray(weights)
        if np.any((weights < 0) | (weights > 1)):
            raise ValueError('weights must be probabilities')
        if not np.allclose(np.sum(weights), 1):
            raise ValueError('weights must sum to 1.0')
        self._weights = weights.copy()
    weights = property(_get_weights, _set_weights)
    def _get_means(self):
        return self._means
    def _set_means(self, means):
        if len(means) != self.components:
            raise ValueError('means must have length equal to number of '
                    'components')
        self._means = np.asarray(means).copy()
    means = property(_get_means, _set_means)
    def _get_covars(self):
        return self._covars
    def _set_covars(self, covars):
        if len(covars) != self.components:
            raise ValueError('covars must have length equal to number of '
                    'components')
        covars = np.asarray(covars)
        if np.any(covars <= 0):
            raise ValueError('covars must be positive')
        self._covars = covars.copy()
    covars = property(_get_covars, _set_covars)
    def fit(self, data, **kwargs):
        ''' 
        fits TGMM to data using the EM algorithm. Additional keyword arguments
        are passed to EM.
        '''
        # using fast Cython implementation by default
        weights, means, sigmas, ll, flag = cEM(data, self.components, self.bounds, 
                **kwargs)
        self.weights = weights
        self.means = means
        self.covars = sigmas ** 2
        if self.bounds is None:
            self.bounds = (np.min(data), np.max(data))
        if not flag:
            warn('EM did not converge (no. iterations = %d' % len(ll), 
                    category=UserWarning)
    def pdf(self, data):
        b = self.bounds
        return np.sum([ w * tnorm_pdf(data, m, s, b) for w, m, s in \
                zip(self.weights, self.means, np.sqrt(self.covars)) ], axis=0)
    def cdf(self, data):
        b = self.bounds
        return np.sum([ w * tnorm_cdf(data, m, s, b) for w, m, s in \
                zip(self.weights, self.means, np.sqrt(self.covars)) ], axis=0)
    def rvs(self, size, prng=np.random):
        ''' uses ancestor and rejection sampling 
        size - shape paramenter
        '''
        if np.isscalar(size):
            size = (size,)
        num = np.prod(size)
        z = randwpmf(self.weights, num, prng=prng)
        rvs = np.empty(num)
        for i in xrange(self.components):
            idx = z == i
            n = np.sum(idx)
            if n == 0:
                continue
            l, u = self.bounds
            m = self.means[i]
            s = np.sqrt(self.covars[i])
            l = (l - m) / s
            u = (u - m) / s
            flag = True
            samples = []
            while n > 0:
                x = prng.randn(n)
                x = x[(x >= l) & (x <= u)]
                n -= len(x)
                if len(x):
                    samples.append(x)
            samples = np.hstack(samples)
            rvs[idx] = samples * s + m
        return rvs.reshape(size)
    def identify(self):
        '''
        Identify mixture components by sorting according to increasing location
        '''
        idx = self.means.argsort()
        self.means = self.means[idx]
        self.covars = self.covars[idx]
        self.weights = self.weights[idx]
    def confint(self, data, level=.95, sample=10000):
        '''
        Confidence intervals via nonparametric bootstrap
        
        Parameters
        ----------
        data   - array of observations
        level  - confidence level
        sample - number of bootstrap samples

        Returns
        -------
        m, c, w - confidence intervals for means (m), covars (c), weights (w)

        Attributes `ci' and `level' are available after calling this method.
        '''
        try:
            self.weights
        except AttributeError:
            raise ValueError('Cannot simulate confidence intervals on unfitted model')
        data = np.ravel(data)
        x = _bootstrap(data, _errworker, sample, (self.components,))
        alpha = norm.ppf(1 - (1 - level) / 2.0) # two-tailed percentile
        err = np.std(x, ddof=1) / np.sqrt(len(x))
        self.ci = tuple(alpha * np.std(x, ddof=1, axis=0))
        self.level = level
        return self.ci
    def kstest(self, data, sample=10000):
        '''
        K-S test via nonparametric bootstrap
        
        Parameters
        ----------
        data   - array of observations
        sample - number of bootstrap samples used for simulating the p-value

        Returns
        -------
        D   - K-S statistic
        p   - one-tailed p-value under the null hypothesis that data is drawn
              from this mixture model (low values of p reject the null
              hypothesis)

        Attributes `ks', `ks_pval' and `ks_sample' are available after calling
        this method.
        '''
        try:
            self.weights
        except AttributeError:
            raise ValueError('Cannot test unfitted model')
        data = np.ravel(data)
        self.ks, _ = kstest(data, self.cdf)
        self.ks_sample = _bootstrap(data, _ksworker, sample, (self.components,))
        self.ks_pval = np.sum(self.D_sample >= D)/float(sample)
        return self.ks, self.ks_pval

def _bootstrap(data, target, sample, args=(), nprocs=None):
    '''
    target is called as target(*args) and may declare a global variable data which
    is a shared array holding the data
    '''
    shdata = Array('d', len(data))
    shdata[:] = data
    pool = Pool(nprocs, _initworker, (shdata,))
    try:
        results = [ pool.apply_async(target, args) for i in xrange(sample) ]
        return [ r.get() for r in results ]
    except KeyboardInterrupt:
        print >> sys.stderr, "killing workers"
        raise
    finally:
        pool.terminate()
        pool.join()

def _initworker(shdata):
    global data
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    data = shdata

def _getsample():
    global data
    _data = np.asarray(data)
    idx = np.random.randint(0, len(data), len(data))
    return _data[idx]

def _ksworker(components):
    sample = _getsample()
    tgmm = TGMM(components)
    tgmm.fit(sample)
    D, p = kstest(sample.ravel(), tgmm.cdf)
    return D

def _errworker(components):
    sample = _getsample()
    tgmm = TGMM(components)
    tgmm.fit(sample)
    tgmm.identify()
    return tgmm.means, tgmm.covars, tgmm.weights

# needed by plot
class tnorm(object):
    def __init__(self, mu, sigma, bounds):
        self.mu = mu
        self.sigma = sigma
        self.bounds = bounds
    def pdf(self, x):
        return tnorm_pdf(x, self.mu, self.sigma, self.bounds)

def make_parser():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('datafile', metavar='data')
    parser.add_argument('components', type=int)
    parser.add_argument('-b', '--bounds', nargs=2, type=float, help='truncates'
            ' data to this interval. (default: takes min and max from data)')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--iterations', type=int, default=100, help='maximum '
            'number of EM iterations (default: %(default)d)')
    parser.add_argument('-l', '--log', action='store_true', help='take log of '
            'data')
    parser.add_argument('--seed', type=int)
    parser.add_argument('-d', '--delimiter', metavar='CHAR', default=',',
            help='input file delimiter (default: \'%(default)s\')')
    parser.add_argument('-p', '--plot', action='store_true')
    parser.add_argument('-o', '--outputfile')
    parser.add_argument('-t', '--title', help='figure title')
    parser.add_argument('-P', '--profile', action='store_true')
    parser.add_argument('-PF', '--prof-file', metavar='FILE', 
            default=os.path.splitext(os.path.basename(__file__))[0]+'.prof')
    parser.add_argument('--nofast', action='store_true', help='Python implementation')
    return parser

def plot(data, model, bins=10, output=None, title=None, **kwargs):
    '''
    produces stacked area plots. 

    Additional parameters are passed to peers.graphics.mixturehist
    '''
    fig = pp.figure()
    means = model.means.ravel()
    deviations = np.sqrt(np.asarray(model.covars).ravel())
    RV = [ tnorm(m, s, model.bounds) for m,s in zip(means, deviations) ]
    mixturehist(data, RV, model.weights, figure=fig, bins=bins, **kwargs)
    pp.xlabel(r'$u = \mathrm{log}(\tau)$ (days)')
    pp.ylabel(r'Prob. Density $p(x)$')
    if title is not None:
        pp.title(sanetext(title))
    pp.draw()
    if output is not None:
        pp.savefig(output, format=fmt(output, 'pdf'))
    pp.show()

def main(args):
    data = np.load(args.datafile, mmap_mode='r')
    if args.log:
        data = np.log(data)
    prng = np.random.RandomState(args.seed)
    if args.nofast:
        weights, means, sigmas, ll, flag = EM(data, args.components, 
                n_iter=args.iterations, prng=prng, verbose=args.verbose)
    else:
        weights, means, sigmas, ll, flag = cEM(data, args.components, 
                n_iter=args.iterations, prng=prng, verbose=args.verbose)
    print
    for i, comp in enumerate(zip(weights, means, sigmas)):
        print 'Component %d:' % (i + 1)
        print '---------------'
        print 'prob. = %g, mean = %g, st.dev = %g' % comp
        w, m, c = comp
        c *= c
        lnmean = timedelta(days=np.exp(m + c / 2))
        lnmed = timedelta(days=np.exp(m))
        lnmod = timedelta(days=np.exp(m - c))
        lnvar = (np.exp(c) - 1) * np.exp(2 * m + c)
        lnerr = timedelta(days=np.sqrt(lnvar))
        print "(Lognorm) mean = %s (1 s.d. = %s)" % (lnmean, lnerr)
        print "(Lognorm) median = %s, mode = %s" % (lnmed, lnmod)
        print 
    print 'Data points: %d' % len(data)
    print 'Log-Likelihood: %g' % ll[-1]
    print 'Minimum value: %s' % timedelta(days=np.exp(data.min()))
    print 'Maximum value: %s' % timedelta(days=np.exp(data.max()))
    print
    if flag:
        print 'EM converged in %d iterations.' % len(ll)
    else:
        print 'EM did NOT converge! Try more iterations.'
    if args.plot:
        tgmm = TGMM(args.components, (np.min(data), np.max(data)))
        tgmm.weights = weights
        tgmm.means = means
        tgmm.covars = sigmas ** 2
        plot(data, tgmm, 50, output=args.outputfile, title=args.title)
    return weights, means, sigmas, ll, flag

if __name__ == '__main__':
    parser = make_parser()
    ns = parser.parse_args()
    if ns.profile:
        from cProfile import runctx
        from pstats import Stats
        runctx('ret = main(ns)', globals=globals(), locals=locals(),
                filename=ns.prof_file)
        Stats(ns.prof_file).strip_dirs().sort_stats("time").print_stats()
        print 'profiling data saved in %s' % ns.prof_file
    else:
        ret = main(ns)

# class TMM(object):
#     def __init__(self, components, bounds=None):
#         '''
#         Parameters
#         ----------
#         components - number of components
#         bounds     - a sequence of (inf, sup) items for as many components in
#                      the model
#         '''
#         super(TMM, self).__init__()
#         self.components = components
#         self.bounds = bounds
#     @classmethod
#     def fromprior(cls,
#             k,
#             hyperweights=None,
#             hypermu=None,
#             hypersigma=None,
#             bounds=None,
#             prng=np.random):
#         ''' Returns a truncated model with k components having random
#         parameters, which are sampled using given hyper-parameters. Priors are:
#         - Dirichlet distribution for the weights : hyperweights are the
#           parameters 'a' and 'b'. 
#         - Exponential for the sigmas : hypersigma is the scale. 
#         - Mu are evenly spaced in the bounds interval plus a 0-mean gaussian
#           noise : hypermu is the standard deviation of this noise. '''
#         hyperweights = hyperweights or np.ones((k,))
#         hypermu = hypermu or 1
#         hypersigma = hypersigma or 1
#         weights = prng.dirichlet(hyperweights)
#         mu = np.linspace(num=k+1,endpoint=0,*bounds)[1:] + prng.randn(k)\
#                 * hypermu
#         sigma = prng.exponential(hypersigma,size=(k,))
#         weights, mu, sigma = map(list,[weights,mu,sigma])
#         return cls.fromvalues(weights,mu,sigma,bounds)
#     def residuals(self, tmm):
#         ''' returns residuals of the parameters between self and given truncated
#         model tmm. '''
#         if self.k != tmm.k:
#             msg = 'cannot compare models with different number of components'
#             raise TMMParamError(msg)
#         if self.bounds[0] < tmm.bounds[1]:
#             if self.bounds[1] < tmm.bounds[0]:
#                 warn('The models have non-overlapping supports',
#                         category=TMMWarning)
#         elif tmm.bounds[1] < self.bounds[0]:
#                 warn('The models have non-overlapping supports',
#                         category=TMMWarning)
#         wres = self.weights - tmm.weights
#         mures = self.mu - tmm.mu
#         sigmares = self.sigma - tmm.sigma
#         return wres, mures, sigmares
#     def plot_EM_info(self):
#         try:
#             self.EM_info
#         except AttributeError:
#             warn('model is not yet fitted. First run EM',category=TMMWarning)
#             return
#         lab = "$\mathbb{E}_z[\mathcal{L}_{x,z}(\mu,\sigma)]$"
#         fig = kwargs.pop('figure',None) or pp.figure()
#         ax =  kwargs.pop('axes',None) or (
#                 fig.axes[0] if len(fig.axes) else fig.add_subplot(111))
#         ax.plot(self.EM_info['ll'],'o:r')
#         ax.set_xlabel('Iterations',fontsize=18)
#         ax.set_ylabel(r'Expected log-likelihood %s' % lab,fontsize=18)
# 
# def draw_residual(args):
#     ''' returns a point from the distribution of residuals of the estimated
#     parameters using a synthetically generated sample of given size.'''
#     size,model,attempts = args
#     while True:
#         try:
#             data = model.rvs(size)
#             emodel = TMM.fromdata(data,model.k,bounds=model.bounds,
#                     attempts=attempts)
#             return size, model.residuals(emodel)
#         except TMMParamError:
#             continue
#         else:
#             break
# 
# def sample_residuals(max,
#         k=None,
#         model=None,
#         base=50,
#         sample=100,
#         attempts=25,
#         **kwargs):
#     ''' Estimates the distribution of residuals of the EM estimator for growing
#     sample sizes. Given a sample size, the distribution of residuals is
#     estimated by producing many synthetic data samples. The sample sizes double
#     max times starting from base, so the computation time depends on the number of
#     EM runs produced, which is given by max * sample.
# 
#     Parameters
#     ----------
#     max      - maximum number of doublings of the sample size
#     k        - optional, sample a model with this number of components
#     model    - optional, use this model 
#     sample   - sample size of the residuals
#     basea    - the initial sample size
#     attempts - how many EM estimates to produce for each data sample. Since
#                   EM converges to a local maximum of the log-likelihood, the
#                   best estimate will be returned
# 
#     Additional kwargs are passed to TMM.fromprior. Note that either one between
#     model and k must be specified '''
#     if (k is None) and (model is None):
#         print 'you must specify a model, or give a number of components'
#         return
#     base = int(base)
#     max = int(max)
#     size = 2**np.arange(max)*base
#     workers = Pool()
#     model = model or TMM.fromprior(k,**kwargs)
#     # the total number of jobs is max * sample
#     args = zip(np.repeat(size,sample), repeat(model), repeat(attempts))
#     # we randomize the jobs so that the last worker doesn't get only big samples
#     # to estimate
#     res = workers.map(draw_residual,np.random.permutation(args))
#     siz, resid = map(np.asarray,zip(*res))
#     idx = np.argsort(siz)
#     siz = siz[idx]
#     resid = np.asarray(resid[idx]).swapaxes(0,1)
#     resid = map(lambda k : np.split(k,max,0), resid)
#     return tuple(np.unique(siz)), tuple(map(np.transpose,resid)), model
# 
# # FIXME <Gio 24 Dic 2009 13:38:00 CET> whiskers should be the 95% confidence
# # intervals, and not 1.5 times the IQR, as by default when using boxplot 
# def add_residuals_boxplot(sizes, resid, scaled=False, figure=None, axes=None):
#     ''' Plots a sequence of boxplots for each size in sizes. Boxplots are
#     computed from each array in resid. If the estimator is asymptotically
#     unbiased, then the distribution of residuals should converge to a delta
#     located at zero as the sample size grows.
# 
#     Parameters
#     ----------
#     sizes  - abscissas
#     resid  - a sequence of arrays for each distribution. If the arrays are 2D,
#              then a new set of axes is created for each subarrays along the
#              second dimension.
#     scaled - if True, compute scaled residuals (default : False)
#     '''
#     fig = figure or pp.figure()
#     scaling = np.abs(resid).max() if scaled else 1.
#     if resid.ndim < 3:
#         resid = resid[np.newaxis]
#     for i,r in enumerate(resid):
#         ax = axes or fig.add_subplot(1,len(resid),i+1)
#         ax.boxplot(r/scaling)
#         ax.axhline(0,ls=':',c='k')
#         if scaled:
#             ax.set_ylim(-1,1)
#         locs,_ = pp.xticks()
#         _,xlabs = pp.xticks(locs,map(str,sizes))
#         _,ylabs = pp.yticks()
# #         for l in xlabs: 
# #             l.set_rotation(-30) 
# #             l.set_fontsize(14)
# #         for l in ylabs:
# #             l.set_fontsize(14)
#     return fig
# 
# def plot_estimbias(max,
#         k=None,
#         model=None,
#         base=50,
#         sample=100,
#         attempts=25,
#         **kwargs):
#     sizes, (w,m,s), model = sample_residuals(max, k, model, sample, attempts, 
#             **kwargs)
#     print model
#     model.plot()
#     add_residuals_boxplot(sizes,m,scaled=1)
#     add_residuals_boxplot(sizes,s,scaled=1)
#     add_residuals_boxplot(sizes,np.vstack(w),scaled=1)
#     return sizes, (w,m,s), model
# #    resid = { 'weights' : {}, 'mu' : {}, 'sigma' : {} }
# #    keys = resid.keys()
# #    titles = { 'mu': r'$\mu_%d$', 'sigma' : r'$\sigma_%d$', 
# #              'weights' : r'$\pi_%d$' }
# 
