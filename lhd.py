# coding=utf-8
# file: lhd.py
# vim:ts=8:sw=4:sts=4

''' Latin Hypercube Designs '''

import numpy as np
from scipy.spatial.distance import pdist

def _map_to_range(lhd, gr):
    lhd_idx = map(tuple, lhd)
    res = []
    for idx in lhd_idx:
        res.append(tuple([ gr[i][k] for i,k in enumerate(idx) ]))
    return np.asarray(res) + np.diff(gr[:,:2],axis=1).T/2

def lhd(m,n,num=None,ranges=None,prng=np.random,maximin=False):
    """
    latin hypercube design in m dimensions.

    Generate (indices of) centers of a latin hypercube design.

    Parameters
    ----------
    m   - non neg. scalar int
          number of dimensions
    n   - non neg. scalar int
          number of points
    num - non neg. scalar or None
          number of LHDs to generate
    ranges - list of m (a,b) tuples
          extrema of the intervals to map the centers into
    prng - instance of numpy.random.RandomState (default = numpy.random)
    maximin - boolean
        if True, returns the design that attains the maximum of mdist

    Returns
    -------
    lhd - (m,n) array
        LHD design
    mdist - float scalar
        minimum pairwise distance over the n points

    Notes
    -----
      By default, the function returns a list of indices into a grid having m
    axis, each of dimension n. If m ranges are passed instead, then the
    coordinates of said centers are returned.
      If num is not None a list of (lhd, mdist) items is generated with
    possible duplicates. Ranges can be indifferently specified as either
    (min,max) or (max,min).

    Examples
    --------
    # list of indices for each grid
    >>> idx = map(tuple,lhd(2,5))
    # coordinates of the first center
    >>> xc,yc = x[idx[0]], y[idx[0]]
    """
    if ranges is not None and len(ranges) != m:
        raise ValueError('expecting %d ranges' % m)
    if ranges is not None:
       gr = np.asarray([ np.linspace(a,b,n,endpoint=False) for (a,b) in
           ranges])
    else:
        gr = None
    if num is None:
        lhd = np.asarray([ prng.permutation(n) for i in xrange(m) ]).T
        if gr is not None:
            lhd = _map_to_range(lhd, gr)
        return pdist(lhd).min(), lhd
    else:
        lhd_iter = ( np.asarray([ prng.permutation(n) for i in xrange(m) ]).T
                for j in xrange(num) )
        if gr is not None:
            lhd_iter = ( _map_to_range(d, gr) for d in lhd_iter )
        lhd_iter = ( (pdist(d).min(), d) for d in lhd_iter )
        if maximin:
            max_d, max_design = -1, None
            for d, design in lhd_iter:
                if d > max_d:
                    max_d = d
                    max_design = design
            return max_d, max_design
        else:
            return list(lhd_iter)
