# -*- coding: utf-8 -*-

# Copyright 1996-2015 PSERC. All rights reserved.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.

# Copyright (c) 2016-2019 by University of Kassel and Fraunhofer Institute for Energy Economics
# and Energy System Technology (IEE), Kassel. All rights reserved.


"""Updates bus, gen, branch data structures to match power flow soln.
"""

from numpy import asarray, angle, pi, conj, zeros, ones, finfo, c_, ix_, real, flatnonzero as find, \
    setdiff1d, intersect1d
from scipy.sparse import csr_matrix

from pandapower.pypower.idx_brch import F_BUS, T_BUS, BR_STATUS, PF, PT, QF, QT
from pandapower.pypower.idx_bus import VM, VA, PD, QD
from pandapower.pypower.idx_gen import GEN_BUS, GEN_STATUS, PG, QG, QMIN, QMAX

EPS = finfo(float).eps


def pfsoln(baseMVA, bus0, gen0, branch0, Ybus, Yf, Yt, V, ref, ref_gens, Ibus=None):
    """Updates bus, gen, branch data structures to match power flow soln.

    @author: Ray Zimmerman (PSERC Cornell)
    @author: Richard Lincoln
    """
    # initialize return values
    bus = bus0
    gen = gen0
    branch = branch0

    # ----- update Qg for all gens and Pg for slack bus(es) -----
    # generator info
    on = find(gen[:, GEN_STATUS] > 0)  # which generators are on?
    gbus = gen[on, GEN_BUS].astype(int)  # what buses are they at?

    # compute total injected bus powers
    Ibus = zeros(len(V)) if Ibus is None else Ibus
    Sbus = V[gbus] * conj(Ybus[gbus, :] * V - Ibus[gbus])

    _update_v(bus, V)
    _update_q(baseMVA, bus, gen, gbus, Sbus, on)
    _update_p(baseMVA, bus, gen, ref, gbus, on, Sbus, ref_gens)

    # ----- update/compute branch power flows -----
    out = find(branch[:, BR_STATUS] == 0)  # out-of-service branches
    br = find(branch[:, BR_STATUS]).astype(int)  # in-service branches

    if len(out):
        raise RuntimeError
    # complex power at "from" bus
    Sf = V[real(branch[br, F_BUS]).astype(int)] * conj(Yf[br, :] * V) * baseMVA
    # complex power injected at "to" bus
    St = V[real(branch[br, T_BUS]).astype(int)] * conj(Yt[br, :] * V) * baseMVA
    branch[ix_(br, [PF, QF, PT, QT])] = c_[Sf.real, Sf.imag, St.real, St.imag]
    branch[ix_(out, [PF, QF, PT, QT])] = zeros((len(out), 4))

    return bus, gen, branch


def _update_v(bus, V):
    # ----- update bus voltages -----
    bus[:, VM] = abs(V)
    bus[:, VA] = angle(V) * 180. / pi


def _update_p(baseMVA, bus, gen, ref, gbus, on, Sbus, ref_gens):
    # update Pg for slack bus(es)
    # inj P + local Pd
    for slack_bus in ref:
        gens_at_bus = find(gbus == slack_bus)  # which is(are) the reference gen(s)?
        p_bus = Sbus[gens_at_bus[0]].real * baseMVA + bus[slack_bus, PD]
        if len(gens_at_bus) > 1:  # more than one generator at this ref bus
            # subtract off what is generated by other gens at this bus
            ext_grids = intersect1d(gens_at_bus, ref_gens)
            pv_gens = setdiff1d(gens_at_bus, ext_grids)
            p_ext_grids = p_bus - sum(gen[pv_gens, PG])
            gen[ext_grids, PG] = p_ext_grids / len(ext_grids)
        else:
            gen[on[gens_at_bus[0]], PG] = p_bus


def _update_q(baseMVA, bus, gen, gbus, Sbus, on):
    # update Qg for all generators
    gen[:, QG] = zeros(gen.shape[0])  # zero out all Qg
    gen[on, QG] = Sbus.imag * baseMVA + bus[gbus, QD]  # inj Q + local Qd
    # ... at this point any buses with more than one generator will have
    # the total Q dispatch for the bus assigned to each generator. This
    # must be split between them. We do it first equally, then in proportion
    # to the reactive range of the generator.

    if len(on) > 1:
        # build connection matrix, element i, j is 1 if gen on(i) at bus j is ON
        nb = bus.shape[0]
        ngon = on.shape[0]
        Cg = csr_matrix((ones(ngon), (range(ngon), gbus)), (ngon, nb))

        # divide Qg by number of generators at the bus to distribute equally
        ngg = Cg * Cg.sum(0).T  # ngon x 1, number of gens at this gen's bus
        ngg = asarray(ngg).flatten()  # 1D array
        gen[on, QG] = gen[on, QG] / ngg

        # divide proportionally
        Cmin = csr_matrix((gen[on, QMIN], (range(ngon), gbus)), (ngon, nb))
        Cmax = csr_matrix((gen[on, QMAX], (range(ngon), gbus)), (ngon, nb))
        Qg_tot = Cg.T * gen[on, QG]  # nb x 1 vector of total Qg at each bus
        Qg_min = Cmin.sum(0).T  # nb x 1 vector of min total Qg at each bus
        Qg_max = Cmax.sum(0).T  # nb x 1 vector of max total Qg at each bus
        Qg_min = asarray(Qg_min).flatten()  # 1D array
        Qg_max = asarray(Qg_max).flatten()  # 1D array
        # gens at buses with Qg range = 0
        ig = find(Cg * Qg_min == Cg * Qg_max)
        Qg_save = gen[on[ig], QG]
        gen[on, QG] = gen[on, QMIN] + (Cg * ((Qg_tot - Qg_min) / (Qg_max - Qg_min + EPS))) * \
                      (gen[on, QMAX] - gen[on, QMIN])  # ^ avoid div by 0
        gen[on[ig], QG] = Qg_save  # (terms are mult by 0 anyway)
