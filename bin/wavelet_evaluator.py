#! /usr/bin/env python3

import rospy
import numpy as np
from pygsp import graphs, filters, reduction
from enum import Enum

class SubmapState(Enum):
    ALL_GOOD = 1
    LOW_GOOD = 2
    HIGH_GOOD = 3
    NO_GOOD = 4

class WaveletEvaluator(object):

    def __init__(self, n_scales = 7):
        self.n_scales = n_scales

    def set_scales(self, n_scales):
        self.n_scales = n_scales

    def compare_signals(self, G, x_1, x_2):

        # Compute the wavelets for each node and scale.
        psi = self.compute_wavelets(G)
        rospy.logdebug(f"[WaveletEvaluator] psi = {psi.shape}")

        # Compute the wavelet coefficients for x_1 and x_2.
        W_1 = self.compute_wavelet_coefficients(psi, x_1)
        W_2 = self.compute_wavelet_coefficients(psi, x_2)
        rospy.logdebug(f"[WaveletEvaluator] W_1 = {W_1.shape}")
        rospy.logdebug(f"[WaveletEvaluator] W_2 = {W_2.shape}")


    def compute_wavelets(self, G):
        rospy.loginfo(f"[WaveletEvaluator] Computing wavelets for {self.n_scales}")
        g = filters.Meyer(G, self.n_scales)

        # Evalute filter bank on the frequencies (eigenvalues).
        f = g.evaluate(G.e)
        f = np.expand_dims(f.T, 1)
        psi = np.zeros((G.N, G.N, self.n_scales))

        for i in range(0, G.N):

            # Create a Dirac centered at node i.
            x = np.zeros((G.N,1))
            x[i] = 1

            # Transform the signal to spectral domain.
            s = G._check_signal(x)
            s = G.gft(s)

            # Multiply the transformed signal with filter.
            if s.ndim == 1:
                s = np.expand_dims(s, -1)
            s = np.expand_dims(s, 1)
            s = np.matmul(s, f)

            # Transform back the features to the vertex domain.
            psi[i, :, :] = G.igft(s).squeeze()

        return psi

    def compute_wavelets_coeffs(self, wavelet, x_signal):
        n_values = x_signal.shape[0]
        W = np.zeros((n_values, self.n_scales))
        for i in range(0, n_values):
            for j in range(0, self.n_scales):
                W[i,j] = np.matmul(wavelet[i,:,j].transpose(), x_signal)

        return W

    def check_submap(self, coeffs_1, coeffs_2, submap_ids):
        submap_coeffs_1 = coeffs_1[submap_ids, :]
        submap_coeffs_2 = coeffs_2[submap_ids, :]

        D = self.compute_cosine_distance(submap_coeffs_1, submap_coeffs_2)
        print(f"Similarity distance: {D.transpose()}")
        return self.evaluate_scales(D)

    def compute_cosine_distance(self, coeffs_1, coeffs_2):
        print(f"c1 {coeffs_1.shape} and c2 {coeffs_2.shape}")
        cosine_distance = np.zeros((self.n_scales, 1))
        for j in range(0, self.n_scales):
            cross = np.dot(coeffs_1[:,j], coeffs_2[:,j])
            n_1 = np.linalg.norm(coeffs_1[:,j])
            n_2 = np.linalg.norm(coeffs_2[:,j])


            cosine_similarity = cross/(n_1*n_2)
            cosine_distance[j] = 1 - cosine_similarity
        return cosine_distance

    def evaluate_scales(self, D):
        k_eps = 0.5

        sum_lower = np.sum(D[0:1])
        sum_higher = np.sum(D[2:])
        sum_total = sum_lower + sum_higher

        if (sum_total < k_eps):
            return SubmapState.ALL_GOOD
        elif (sum_lower < k_eps and sum_higher >= k_eps):
            return SubmapState.LOW_GOOD
        elif (sum_lower >= k_eps and sum_higher < k_eps):
            return SubmapState.HIGH_GOOD
        else:
            return SubmapState.NO_GOOD

if __name__ == '__main__':
    print(f" --- Test Driver for the Wavelet Evaluator ----------------------")
    eval = WaveletEvaluator()

    # Create a reduced graph for quicker tests.
    G = graphs.Bunny()
    ind = np.arange(0, G.N, 10)
    Gs = reduction.kron_reduction(G, ind)
    Gs.compute_fourier_basis()


    # Compute wavelets.
    psi = eval.compute_wavelets(Gs)
    print(f" psi = {psi.shape}")

    # Compute wavelet coefficients for two signals.
    x_1 = Gs.coords
    x_1 = np.linalg.norm(x_1, ord=2, axis=1)
    x_2 = Gs.coords + 10
    x_2 = np.linalg.norm(x_2, ord=2, axis=1)

    W_1 = eval.compute_wavelets_coeffs(psi, x_1)
    W_2 = eval.compute_wavelets_coeffs(psi, x_2)
    print(f" W_1 = {W_1.shape} andd W_2 = {W_2.shape}")

    ids = np.array([0,1,2,3,4,5], dtype=np.intp)
    print(f"Checking submap for indices: {ids}")
    eval_code = eval.check_submap(W_1, W_2, ids)
    print(f"Submap state code: {eval_code}")
