#! /usr/bin/env python2

import rospy
import numpy as np
from pygsp import graphs, filters, reduction
from enum import Enum

import pandas
import scipy.spatial
import pickle

class SubmapState(Enum):
    ALL_GOOD = 1
    LOW_GOOD = 2
    HIGH_GOOD = 3
    NO_GOOD = 4

class WaveletEvaluator(object):

    def __init__(self, n_scales = 7):
        self.n_scales = n_scales
        self.psi = None
        self.feature_names = ['Euclidean_L', 'Euclidean_B', 'Euclidean_H','Correlation_L', 'Correlation_B', 'Correlation_H', 'Manhattan_L', 'Manhattan_B', 'Manhattan_H', 'Chebyshev_L', 'Chebyshev_B', 'Chebyshev_H']

    def set_scales(self, n_scales):
        self.n_scales = n_scales

    def compare_signals(self, G, x_1, x_2):
        # Compute the wavelets for each node and scale.
        psi = self.compute_wavelets(G)
        rospy.logdebug("[WaveletEvaluator] psi = {psi}".format(psi=psi.shape))

        # Compute the wavelet coefficients for x_1 and x_2.
        W_1 = self.compute_wavelet_coeffs(psi, x_1)
        W_2 = self.compute_wavelet_coeffs(psi, x_2)

    def get_wavelets(self):
        return self.psi

    def compute_wavelets(self, G):
        rospy.loginfo("[WaveletEvaluator] Computing wavelets for {n_scales} scales.".format(n_scales=self.n_scales))
        g = filters.Meyer(G, self.n_scales)

        # Evalute filter bank on the frequencies (eigenvalues).
        f = g.evaluate(G.e)
        f = np.expand_dims(f.T, 1)
        self.psi = np.zeros((G.N, G.N, self.n_scales))

        for i in range(0, G.N):
            # Create a Dirac centered at node i.
            x = np.zeros((G.N,1))
            x[i] = 1

            # Transform the signal to spectral domain.
            s = G.gft(x)

            # Multiply the transformed signal with filter.
            if s.ndim == 1:
                s = np.expand_dims(s, -1)
            s = np.expand_dims(s, 1)
            s = np.matmul(s, f)

            # Transform back the features to the vertex domain.
            self.psi[i, :, :] = G.igft(s).squeeze()

        return self.psi

    def compute_wavelet_coeffs(self, x_signal):
        return self.compute_wavelet_coeffs_using_wavelet(self.psi, x_signal)

    def compute_wavelet_coeffs_using_wavelet(self, wavelet, x_signal):
        n_values = x_signal.shape[0]
        n_dim = x_signal.shape[1] if len(x_signal.shape) >= 2 else 1
        W = np.zeros((n_values, self.n_scales, n_dim)).squeeze()
        for i in range(0, n_values):
            for j in range(0, self.n_scales):
                W[i,j] = np.matmul(wavelet[i,:,j].transpose(), x_signal)

        return W if n_dim == 1 else np.mean(W, axis=2)

    def check_submap(self, coeffs_1, coeffs_2, submap_ids):
        submap_coeffs_1 = coeffs_1[submap_ids, :]
        submap_coeffs_2 = coeffs_2[submap_ids, :]

        D = self.compute_cosine_distance(submap_coeffs_1, submap_coeffs_2)
        return self.evaluate_scales(D)

    def compute_distances(self, coeffs_1, coeffs_2):
        distances = np.zeros((9, self.n_scales))
        for j in range(0, self.n_scales):
            distances[1, j] = scipy.spatial.distance.euclidean(coeffs_1[:,j], coeffs_2[:,j])
            distances[3, j] = scipy.spatial.distance.correlation(coeffs_1[:,j], coeffs_2[:,j])
            distances[7, j] = scipy.spatial.distance.cityblock(coeffs_1[:,j], coeffs_2[:,j])
            distances[8, j] = scipy.spatial.distance.chebyshev(coeffs_1[:,j], coeffs_2[:,j])

        return distances

    def compute_distances_1D(self, coeffs_1, coeffs_2):
        distances = np.zeros((9, self.n_scales))
        for j in range(0, self.n_scales):
            distances[0, j] = scipy.spatial.distance.euclidean(coeffs_1[j], coeffs_2[j])
            distances[1, j] = scipy.spatial.distance.correlation(coeffs_1[j], coeffs_2[j])
            distances[2, j] = scipy.spatial.distance.cityblock(coeffs_1[j], coeffs_2[j])
            distances[3, j] = scipy.spatial.distance.chebyshev(coeffs_1[j], coeffs_2[j])

        return distances

    def compute_features_for_submap(self, coeffs_1, coeffs_2, submap_ids):
        n_coeffs_1 = coeffs_1.shape[0]
        n_coeffs_2 = coeffs_2.shape[0]
        if n_coeffs_1 != n_coeffs_2:
            return pandas.DataFrame({})

        submap_ids = np.array(submap_ids, dtype=np.int)
        mask_oob = submap_ids < n_coeffs_1
        mask_oob = np.logical_and(mask_oob, submap_ids < n_coeffs_2)
        submap_ids = submap_ids[mask_oob]

        submap_coeffs_1 = coeffs_1[submap_ids, :]
        submap_coeffs_2 = coeffs_2[submap_ids, :]
        return self.compute_features(submap_coeffs_1, submap_coeffs_2)

    def compute_features(self, submap_coeffs_1, submap_coeffs_2):
        n_nodes = submap_coeffs_1.shape[0]
        all_data = pandas.DataFrame()
        for i in range(n_nodes):
            D = self.compute_distances_1D(submap_coeffs_1[i,:], submap_coeffs_2[i,:])
#             print(f'D is {D[:,2:4]}')
            D = np.nan_to_num(D)
            data = pandas.DataFrame({
                # Euclidean distance.
                self.feature_names[0]:[np.sum(D[0, 0:2])],
                self.feature_names[1]:[np.sum(D[0, 2:4])],
                self.feature_names[2]:[np.sum(D[0, 5:])],

                # Correlation.
                self.feature_names[3]:[np.sum(D[1, 0:2])],
                self.feature_names[4]:[np.sum(D[1, 2:4])],
                self.feature_names[5]:[np.sum(D[1, 5:])],

                # Cityblock distance.
                self.feature_names[6]:[np.sum(D[2, 0:2])],
                self.feature_names[7]:[np.sum(D[2, 2:4])],
                self.feature_names[8]:[np.sum(D[2, 5:])],

                # Chebyshev distance.
                self.feature_names[9]:[np.sum(D[3, 0:2])],
                self.feature_names[10]:[np.sum(D[3, 2:4])],
                self.feature_names[11]:[np.sum(D[3, 5:])],
            })
            all_data = all_data.append(data)
        return np.nan_to_num(all_data)

    def classify_simple(self, data):
        n_nodes = data.shape[0]
        labels = []
        for i in range(0, n_nodes):
            low_mean = data[i,0]
            mid_mean = data[i,1]
            high_mean = data[i,2]
            dists = np.array([low_mean, mid_mean, high_mean])
            max_dist_idx = np.argmax(dists)

            np.set_printoptions(suppress=True)
            rospy.loginfo('[WaveletEvaluator] dists are {dists}'.format(dists=dists))
            local_labels = []
            # h floor
            # if dists[0] > 0.4:
            #     local_labels.append(1)
            # if dists[1] > 0.11: # for h_naymal_2 we had 0.2
            #     local_labels.append(2)
            # if dists[2] > 0.05:
            #     local_labels.append(3)

            if dists[0] > 0.5:
                local_labels.append(1)
            if dists[1] > 0.21: # for h_naymal_2 we had 0.2
                local_labels.append(2)
            if dists[2] > 0.11:
                local_labels.append(3)

            # -----------------------------------------------

            # hagerbach anymal 2
            # if dists[0] > 0.31:
            #     local_labels.append(1)
            # if dists[1] > 0.21: # for h_naymal_2 we had 0.2
            #     local_labels.append(2)
            # if dists[2] > 0.037:
            #     local_labels.append(3)

            # hagerbach anymal 1
            # if dists[0] > 2.96:
            #     local_labels.append(1)
            # if dists[1] > 0.80: # for h_naymal_2 we had 0.2
            #     local_labels.append(2)
            # if dists[2] > 0.025:
            #     local_labels.append(3)

            # h floor
            # if dists[0] > 0.31:
            #     local_labels.append(1)
            # if dists[1] > 0.21: # for h_naymal_2 we had 0.2
            #     local_labels.append(2)
            # if dists[2] > 0.07:
            #     local_labels.append(3)

            rospy.loginfo('local labels are {local_labels}'.format(local_labels=local_labels))
            labels.append(local_labels)
        return labels

if __name__ == '__main__':
    print(" --- Test Driver for the Wavelet Evaluator ----------------------")
    eval = WaveletEvaluator()

    # Create a reduced graph for quicker tests.
    G = graphs.Bunny()
    ind = np.arange(0, G.N, 10)
    Gs = reduction.kron_reduction(G, ind)
    Gs.compute_fourier_basis()


    # Compute wavelets.
    psi = eval.compute_wavelets(Gs)

    # Compute wavelet coefficients for two signals.
    x_1 = Gs.coords
    x_1 = np.linalg.norm(x_1, ord=2, axis=1)
    x_2 = Gs.coords + 10
    x_2 = np.linalg.norm(x_2, ord=2, axis=1)

    W_1 = eval.compute_wavelet_coeffs_using_wavelet(psi, x_1)
    W_2 = eval.compute_wavelet_coeffs_using_wavelet(psi, x_2)
    features = eval.compute_features(W_1, W_2)
    label = eval.classify_simple(features)
