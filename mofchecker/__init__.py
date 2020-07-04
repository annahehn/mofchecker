# -*- coding: utf-8 -*-
import os
import warnings
from pathlib import Path

import numpy as np
from pymatgen import Structure
from pymatgen.analysis.local_env import CrystalNN, LocalStructOrderParams
from pymatgen.io.cif import CifParser

from .definitions import OP_DEF
from .utils import (HighCoordinationNumber, LowCoordinationNumber, NoMetal,
                    NoOpenDefined, get_overlaps)


class MOFChecker:
    def __init__(self, structure, porous_adjustment=True):
        self.structure = structure
        self.metal_indices = [
            i for i, species in enumerate(self.structure.species)
            if species.is_metal
        ]
        self.porous_adjustment = porous_adjustment
        self.metal_features = None
        self._open_indices = set()
        self._has_oms = None
        self._filename = None
        self._atomic_overlaps = None
        self._name = None
        self.c_indices = [
            i for i, species in enumerate(self.structure.species)
            if str(species) == 'C'
        ]
        self.h_indices = [
            i for i, species in enumerate(self.structure.species)
            if str(species) == 'H'
        ]
        self._overvalent_c = None

    def _set_filename(self, path):
        self._filename = os.path.abspath(path)
        self._name = Path(path).stem

    def _get_atomic_overlaps(self):
        if self._atomic_overlaps is not None:
            return self._atomic_overlaps

        self._atomic_overlaps = get_overlaps(self.structure)
        return self._atomic_overlaps

    @property
    def has_atomic_overlaps(self):
        atomic_overlaps = self._get_atomic_overlaps()
        return len(atomic_overlaps) > 0

    @property
    def name(self):
        return self._name

    @property
    def has_carbon(self):
        return len(self.c_indices) > 0

    @property
    def has_hydrogen(self):
        return len(self.h_indices) > 0

    @property
    def has_overvalent_c(self):
        if self._overvalent_c is not None:
            return self._overvalent_c

        self._has_overvalent_c()
        return self._overvalent_c

    def _has_overvalent_c(self):
        overvalent_c = False
        for site_index in self.c_indices:
            cn = self.get_cn(site_index)
            if cn > 4:
                overvalent_c = True
                break
        self._overvalent_c = overvalent_c

    @classmethod
    def _from_file(cls, path, porous_adjustment=True):
        s = Structure.from_file(path)
        omscls = cls(s, porous_adjustment)
        omscls._set_filename(path)  # pylint:disable=protected-access
        return omscls

    @classmethod
    def from_cif(cls, path, porous_adjustment=True):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            cifparser = CifParser(path)
            s = cifparser.get_structures()[0]
            omscls = cls(s, porous_adjustment)
            omscls._set_filename(path)  # pylint:disable=protected-access
            return omscls

    def get_cn(self, site_index):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            cnn = CrystalNN(porous_adjustment=self.porous_adjustment)
            return cnn.get_cn(self.structure, site_index)

    def _get_ops_for_site(self, site_index):
        cn = self.get_cn(site_index)
        try:
            names = OP_DEF[cn]['names']
            is_open = OP_DEF[cn]['open']
            weights = OP_DEF[cn]['weights']
            lsop = LocalStructOrderParams(names)
            return (cn, names,
                    lsop.get_order_parameters(self.structure,
                                              site_index), is_open, weights)
        except KeyError:
            # For a bit more fine grained error messages
            if cn <= 3:  # pylint:disable=no-else-raise
                raise LowCoordinationNumber(
                    'Coordination number {} is low and order parameters undefined'
                    .format(cn))
            elif cn > 8:
                raise HighCoordinationNumber(
                    'Coordination number {} is high and order parameters undefined'
                    .format(cn))

            return cn, None, None, None, None

    def is_site_open(self, site_index):
        if site_index not in self._open_indices:
            try:
                _, _, lsop, is_open, weights = self._get_ops_for_site(
                    site_index)
                site_open = MOFChecker._check_if_open(lsop, is_open, weights)
                if site_open:
                    self._open_indices.add(site_index)
                return site_open
            except LowCoordinationNumber:
                return True
            except HighCoordinationNumber:
                return None
        return True

    @staticmethod
    def _check_if_open(lsop, is_open, weights, threshold=0.5):
        if lsop is not None:
            if is_open is None:
                return False
            lsop = np.array(lsop) * np.array(weights)
            open_contributions = lsop[is_open].sum()
            close_contributions = lsop.sum() - open_contributions
            return open_contributions / (open_contributions +
                                         close_contributions) > threshold
        return None

    def _get_metal_descriptors_for_site(self, site_index):
        metal = str(self.structure[site_index].species)
        try:
            cn, names, lsop, is_open, weights = self._get_ops_for_site(
                site_index)
            site_open = MOFChecker._check_if_open(lsop, is_open, weights)
            if site_open:
                self._open_indices.add(site_index)
            descriptors = {
                'metal': metal,
                'lsop': dict(zip(names, lsop)),
                'open': site_open,
                'cn': cn
            }
        except LowCoordinationNumber:
            descriptors = {
                'metal': metal,
                'lsop': None,
                'open': True,
                'cn': None
            }
        except HighCoordinationNumber:
            descriptors = {
                'metal': metal,
                'lsop': None,
                'open': None,
                'cn': None
            }
        return descriptors

    def get_metal_descriptors_for_site(self, site_index):
        if not self.has_metal():
            raise NoMetal
        return self._get_metal_descriptors_for_site(site_index)

    def _get_metal_descriptors(self):
        descriptordict = {}
        for site_index in self.metal_indices:
            descriptordict[site_index] = self._get_metal_descriptors_for_site(
                site_index)

        self.metal_features = descriptordict

        return descriptordict

    def get_metal_descriptors(self):
        if not self.has_metal():
            raise NoMetal
        return self._get_metal_descriptors()

    def has_metal(self):
        if self.metal_indices:
            return True
        return False

    @property
    def has_oms(self):
        if not self.has_metal():
            raise NoMetal('This structure does not contain a metal')
        if self._has_oms is not None:  # pylint:disable=no-else-return
            return self._has_oms
        else:
            for site_index in self.metal_indices:
                if self.is_site_open(site_index):
                    self._has_oms = True
                    return True
            self._has_oms = False
            return False