#! /usr/bin/env python2
import os
import time

import numpy as np
import pandas
import rospy
from nav_msgs.msg import Path
from maplab_msgs.msg import Graph, Trajectory, TrajectoryNode, SubmapConstraint
from multiprocessing import Lock

from global_graph import GlobalGraph
from signal_handler import SignalHandler
from signal_synchronizer import SignalSynchronizer
from wavelet_evaluator import WaveletEvaluator
from simple_classifier import SimpleClassifier
from top_classifier import TopClassifier
from command_post import CommandPost
from classification_result import ClassificationResult
from constraint_handler import ConstraintHandler
from config import ClientConfig
from plotter import Plotter
from utils import Utils

class GraphClient(object):
    def __init__(self):
        self.is_initialized = False
        self.is_updating = False
        self.last_update_seq = -1
        self.config = ClientConfig()
        self.config.init_from_config()
        Plotter.PlotClientBanner()
        Plotter.PrintClientConfig(self.config)
        Plotter.PrintSeparator()

        self.mutex = Lock()
        self.constraint_mutex = Lock()
        self.mutex.acquire()

        # Subscriber and publisher
        rospy.Subscriber(self.config.opt_graph_topic, Graph, self.global_graph_callback)
        rospy.Subscriber(self.config.opt_traj_topic, Trajectory, self.traj_opt_callback)
        rospy.Subscriber(self.config.est_traj_topic, Trajectory, self.traj_callback)
        rospy.Subscriber(self.config.est_traj_path_topic, Path, self.traj_path_callback)
        if self.config.enable_submap_constraints:
            rospy.Subscriber(self.config.submap_constraint_topic, SubmapConstraint, self.submap_constraint_callback)
            self.constraint_handler = ConstraintHandler()

        self.intra_constraint_pub = rospy.Publisher(self.config.intra_constraint_topic, Path, queue_size=20)

        if self.config.enable_client_update:
            rospy.Subscriber(self.config.client_update_topic, Graph, self.client_update_callback)
            self.client_update_pub = rospy.Publisher(self.config.client_update_topic, Graph, queue_size=20)

        # Handlers and evaluators.
        self.global_graph = GlobalGraph(self.config, reduced=False)
        self.robot_graph = GlobalGraph(self.config, reduced=False)
        self.latest_traj_msg = None
        self.signal = SignalHandler(self.config)
        self.optimized_signal = SignalHandler(self.config)
        self.synchronizer = SignalSynchronizer(self.config)
        self.eval = WaveletEvaluator()
        self.robot_eval = WaveletEvaluator()
        self.commander = CommandPost()

        # self.classifier = SimpleClassifier()
        self.classifier = TopClassifier(10)

        # Key management to keep track of the received messages.
        self.optimized_keys = []
        self.keys = []

        self.create_data_export_folder()
        self.mutex.release()
        self.is_initialized = True


    def create_data_export_folder(self):
        if not self.config.enable_signal_recording and not self.config.enable_trajectory_recording:
            return
        cur_ts = Utils.ros_time_to_ns(rospy.Time.now())
        export_folder = self.config.dataroot + '/data/' + self.config.robot_name + '_%d'%np.float32(cur_ts)
        rospy.logwarn('[GraphClient] Setting up dataroot folder to {export_folder}'.format(export_folder=export_folder))
        if not os.path.exists(export_folder):
            os.mkdir(export_folder)
            os.mkdir(export_folder + '/data')
        self.config.dataroot = export_folder

    def global_graph_callback(self, msg):
        rospy.loginfo("[GraphClient] Received graph message from monitor {frame}.".format(frame=msg.header.frame_id))
        if not (self.is_initialized and(self.config.enable_anchor_constraints or self.config.enable_relative_constraints)):
            return
        self.mutex.acquire()

        # We only trigger the graph building if the msg contains new information.
        if self.global_graph.msg_contains_updates(msg) and self.config.client_mode == 'multiscale':
            self.global_graph.build(msg)
            self.record_signal_for_key(np.array([0]), 'opt')
            self.eval.compute_wavelets(self.global_graph.G)

        self.mutex.release()

    def client_update_callback(self, graph_msg):
        if not (self.is_initialized and (self.config.enable_anchor_constraints or self.config.enable_relative_constraints)):
            return
        # Theoretically this does exactly the same as the graph_callback, but
        # lets separate it for now to be a bit more flexible.
        rospy.loginfo('[GraphClient] Received client update')
        client_seq = graph_msg.header.seq
        self.mutex.acquire()
        graph_seq = self.global_graph.graph_seq
        if self.global_graph.msg_contains_updates(graph_msg) and self.config.client_mode == 'multiscale':
            self.global_graph.build(graph_msg)
            self.eval.compute_wavelets(self.global_graph.G)

        self.mutex.release()

    def traj_opt_callback(self, msg):
        if not (self.is_initialized and (self.config.enable_anchor_constraints or self.config.enable_relative_constraints)):
            return

        keys = self.optimized_signal.convert_signal(msg)
        rospy.loginfo("[GraphClient] Received opt trajectory message from {keys}.".format(keys=keys))

        for key in keys:
            if self.key_in_optimized_keys(key):
                continue
            self.optimized_keys.append(key)

    def traj_callback(self, msg):
        if self.is_initialized is False:
            return

        key = self.signal.convert_signal(msg)
        if self.key_in_keys(key):
            return
        self.keys.append(key)

    def traj_path_callback(self, msg):
        if not (self.is_initialized and (self.config.enable_anchor_constraints or self.config.enable_relative_constraints)):
            return
        self.latest_traj_msg = msg

    def process_latest_robot_data(self):
        if self.latest_traj_msg == None:
            return False

        key = self.signal.convert_signal_from_path(self.latest_traj_msg, self.config.robot_name)
        if not key:
            rospy.logerror("[GraphClient] Unable to convert msg to signal.")
            return False

        if self.key_in_keys(key):
            return True
        self.keys.append(key)
        return True

    def submap_constraint_callback(self, msg):
        if not (self.is_initialized and self.config.enable_submap_constraints):
            rospy.loginfo("[GraphClient] Received submap constraint message before being initialized.")
            return
        rospy.loginfo("[GraphClient] Received submap constraint message.")
        self.constraint_mutex.acquire()
        self.constraint_handler.add_constraints(msg)
        self.constraint_mutex.release()

    def update(self):
        self.mutex.acquire()
        if self.is_updating:
            self.mutex.release()
            return
        print('[GraphClient] {last_upd} and {graph_upd})'.format(last_upd=self.last_update_seq, graph_upd=self.global_graph.graph_seq))
        # if self.last_update_seq > 0 and self.last_update_seq == self.global_graph.graph_seq:
            # self.mutex.release()
            # return
        self.is_updating = True
        self.mutex.release()

        rospy.loginfo("[GraphClient] Updating...")
        self.commander.reset_msgs()
        # self.update_degenerate_anchors()

        if not self.process_latest_robot_data():
            rospy.logwarn('[GraphClient] Unable to process latest robot data.')
            self.mutex.acquire()
            self.is_updating = False
            self.mutex.release()
            return

        self.compare_estimations()
        # self.publish_client_update()

        n_constraints = self.commander.get_total_amount_of_constraints()
        if n_constraints > 0:
            rospy.loginfo('[GraphClient] Updating completed (sent {n_constraints} constraints)'.format(n_constraints=n_constraints))
            rospy.loginfo('[GraphClient] In detail relatives: {n_low} / {n_mid} / {n_high}'.format(n_low=self.commander.small_constraint_counter, n_mid=self.commander.mid_constraint_counter, n_high=self.commander.large_constraint_counter))
            rospy.loginfo('[GraphClient] In detail anchors: {n_anchor}'.format(n_anchor=self.commander.anchor_constraint_counter))
        self.mutex.acquire()
        self.is_updating = False
        self.last_update_seq = self.global_graph.graph_seq
        self.mutex.release()

    def record_all_signals(self, x_est, x_opt):
        if not self.config.enable_signal_recording:
            return
        self.record_signal_for_key(x_est, 'est')
        self.record_signal_for_key(x_opt, 'opt')

    def record_raw_est_trajectory(self, traj):
        filename = self.config.dataroot + self.config.trajectory_raw_export_path.format(src='est')
        np.save(filename, traj)

    def record_synchronized_trajectories(self, traj_est, traj_opt):
        if not self.config.enable_trajectory_recording:
            return
        self.record_traj_for_key(traj_est, 'est')
        self.record_traj_for_key(traj_opt, 'opt')

    def record_signal_for_key(self, x, src):
        signal_file = self.config.dataroot + self.config.signal_export_path.format(src=src)
        np.save(signal_file, x)
        graph_coords_file = self.config.dataroot + self.config.graph_coords_export_path.format(src=src)
        graph_adj_file = self.config.dataroot + self.config.graph_adj_export_path.format(src=src)
        if src == 'opt':
            self.global_graph.write_graph_to_disk(graph_coords_file, graph_adj_file)
        elif src == 'est':
            self.robot_graph.write_graph_to_disk(graph_coords_file, graph_adj_file)
        rospy.logwarn('[GraphClient] for {src} we have {x_shape} and {coords_shape}'.format(src=src, x_shape=x.shape, coords_shape=self.robot_graph.coords.shape))

    def record_traj_for_key(self, traj, src):
        filename = self.config.dataroot + self.config.trajectory_export_path.format(src=src)
        np.save(filename, traj)

    def record_features(self, features):
        filename = self.config.dataroot + "/data/features.npy"
        np.save(filename, features)

    def compare_estimations(self):
        if not self.config.enable_relative_constraints:
            return
        rospy.loginfo("[GraphClient] Comparing estimations.")
        self.mutex.acquire()
        if self.global_graph.is_built is False and self.config.client_mode == 'multiscale':
            rospy.logerr('[GraphClient] Graph is not built yet.')
            self.mutex.release()
            return
        # Check whether we have an optimized version of it.
        if self.key_in_optimized_keys(self.config.robot_name):
            self.compare_stored_signals(self.config.robot_name)
        else:
            rospy.logwarn("[GraphClient] Found no optimized version of {robot} for comparison.".format(robot=self.config.robot_name))
        self.mutex.release()

    def check_for_submap_constraints(self, labels, all_opt_nodes):
        if not self.config.enable_submap_constraints:
            return
        # self.constraint_mutex.acquire()
        # path_msgs = self.constraint_handler.create_msg_for_intra_constraints(self.config.robot_name, labels, all_opt_nodes)
        # self.constraint_mutex.release()
        # for msg in path_msgs:
        #     self.intra_constraint_pub.publish(msg)
        #     self.commander.add_to_constraint_counter(0,0,len(msg.poses))
        #     time.sleep(0.01)

    def publish_client_update(self):
        if not (self.config.enable_anchor_constraints and self.global_graph.is_built and self.config.enable_client_update):
            return
        self.mutex.acquire()
        graph_msg = self.global_graph.latest_graph_msg
        if graph_msg is not None:
            self.client_update_pub.publish(graph_msg)
        self.mutex.release()

    def compare_stored_signals(self, key):
        rospy.logwarn("[GraphClient] Comparing signals for {key}.".format(key=key))
        # Retrieve the estimated and optimized versions of the trajectory.
        all_est_nodes = self.signal.get_all_nodes(key)
        all_opt_nodes = self.optimized_signal.get_all_nodes(key)
        n_opt_nodes = len(all_opt_nodes)


        # Compute the features and publish the results.
        # This evaluates per node the scale of the difference
        # and creates a relative constraint accordingly.
        self.record_raw_est_trajectory(self.signal.compute_trajectory(all_est_nodes))
        all_opt_nodes, all_est_nodes = self.reduce_and_synchronize(all_opt_nodes, all_est_nodes)
        if all_opt_nodes is None or all_est_nodes is None:
            rospy.logerr('[GraphClient] Synchronization failed')
            return False

        labels = self.compute_all_labels(key, all_opt_nodes, all_est_nodes)
        self.evaluate_and_publish_features(labels)

        # Check if we the robot identified a degeneracy in its state.
        # Publish an anchor node curing the affected areas.
        self.check_for_degeneracy(all_opt_nodes, all_est_nodes)

        # Check for large discrepancies in the data.
        # If so publish submap constraints.
        self.check_for_submap_constraints(labels, all_opt_nodes)

        return True

    def reduce_and_synchronize(self, all_opt_nodes, all_est_nodes):
        (all_opt_nodes, all_est_nodes, opt_idx, est_idx) = self.synchronizer.synchronize(all_opt_nodes, all_est_nodes)
        n_nodes = len(all_est_nodes)
        assert(n_nodes == len(all_opt_nodes))
        assert(len(est_idx) == len(opt_idx))
        if n_nodes == 0:
            rospy.logwarn('[GraphClient] Could not synchronize nodes.')
            return (None, None)

        # Reduce the robot graph and compute the wavelet basis functions.
        positions = np.array([np.array(x.position) for x in all_est_nodes])
        orientations = np.array([np.array(x.orientation) for x in all_est_nodes])
        timestamps = np.array([np.array(Utils.ros_time_to_ns(x.ts)) for x in all_est_nodes])
        poses = np.column_stack([positions, orientations, timestamps])

        self.robot_graph.build_from_poses(poses)
        # self.robot_graph.reduce_graph_using_indices(est_idx)

        # TODO(lbern): fix this temporary test
        # Due to the reduction we rebuild here.
        positions = np.array([np.array(x.position) for x in all_opt_nodes])
        orientations = np.array([np.array(x.orientation) for x in all_opt_nodes])
        timestamps = np.array([np.array(Utils.ros_time_to_ns(x.ts)) for x in all_opt_nodes])
        global_poses = np.column_stack([positions, orientations, timestamps])
        self.global_graph.build_from_poses(global_poses)

        if self.config.client_mode == 'multiscale':
            self.eval.compute_wavelets(self.global_graph.G)
            self.robot_eval.compute_wavelets(self.robot_graph.G)
        return (all_opt_nodes, all_est_nodes)

    def check_for_degeneracy(self, all_opt_nodes, all_est_nodes):
        if not self.config.enable_anchor_constraints:
            return
        rospy.loginfo('[GraphClient] Checking for degeneracy.')
        n_nodes = len(all_opt_nodes)
        assert n_nodes == len(all_est_nodes)
        for i in range(0, n_nodes):
            if not all_est_nodes[i].degenerate:
                continue
            pivot = self.config.degenerate_window // 2
            begin_send = max(i - pivot, 0)
            end_send = min(i + (self.config.degenerate_window - pivot), n_nodes)
            rospy.logerr('[GraphClient] Sending degenerate anchros from {begin_send} to {end_send}'.format(begin_send=begin_send, end_send=end_send))
            self.commander.send_anchors(all_opt_nodes, begin_send, end_send)

    def update_degenerate_anchors(self):
        all_opt_nodes = self.optimized_signal.get_all_nodes(self.config.robot_name)
        if len(all_opt_nodes) == 0:
            rospy.logerr('[GraphClient] Robot {robot} does not have any optimized nodes yet.'.format(robot=self.config.robot_name))
            return
        self.commander.update_degenerate_anchors(all_opt_nodes)

    def compute_all_labels(self, key, all_opt_nodes, all_est_nodes):
        if self.config.client_mode == 'multiscale':
            return self.perform_multiscale_evaluation(key, all_opt_nodes, all_est_nodes)
        elif self.config.client_mode == 'euclidean':
            return self.perform_euclidean_evaluation(key, all_opt_nodes, all_est_nodes)
        elif self.config.client_mode == 'always':
            return self.perform_relative(key, all_opt_nodes, all_est_nodes)
        elif self.config.client_mode == 'absolute':
            return self.perform_absolute(key, all_opt_nodes, all_est_nodes)
        else:
            rospy.logerr('[GraphClient] Unknown mode specified {mode}'.format(mode=self.config.client_mode))
            return None

    def perform_multiscale_evaluation(self, key, all_opt_nodes, all_est_nodes):
        # Compute the signal using the synchronized estimated nodes.
        x_est = self.signal.compute_signal(all_est_nodes)
        x_opt = self.optimized_signal.compute_signal(all_opt_nodes)

        self.record_all_signals(x_est, x_opt)
        self.record_synchronized_trajectories(self.signal.compute_trajectory(all_est_nodes), self.optimized_signal.compute_trajectory(all_opt_nodes))

        psi = self.eval.get_wavelets()
        robot_psi = self.robot_eval.get_wavelets()
        n_dim = psi.shape[0]
        if n_dim != x_est.shape[0] or n_dim != x_opt.shape[0]:
            rospy.logwarn('[GraphClient] We have a size mismatch: {n_dim} vs. {x_est} vs. {x_opt}. Trying to fix it.'.format(n_dim=n_dim, x_est=x_est.shape[0], x_opt=x_opt.shape[0]))

            positions = np.array([np.array(x.position) for x in all_opt_nodes])
            self.global_graph.build_from_poses(positions)
            self.eval.compute_wavelets(self.global_graph.G)
            psi = self.eval.get_wavelets()
            n_dim = psi.shape[0]

        if n_dim != robot_psi.shape[0] or psi.shape[1] != robot_psi.shape[1]:
            rospy.logwarn('[GraphClient] Optimized wavelet does not match robot wavelet: {psi} vs. {robot_psi}'.format(psi=psi.shape, robot_psi=robot_psi.shape))
            return None

        # Compute all the wavelet coefficients.
        # We will filter them later per submap.
        W_est = self.robot_eval.compute_wavelet_coeffs(x_est)
        W_opt = self.eval.compute_wavelet_coeffs(x_opt)
        features = self.eval.compute_features(W_opt, W_est)
        self.record_features(features)

        labels =  self.classifier.classify(features)
        return ClassificationResult(key, all_opt_nodes, features, labels)

    def perform_euclidean_evaluation(self, key, all_opt_nodes, all_est_nodes):
        est_traj = self.optimized_signal.compute_trajectory(all_opt_nodes)
        opt_traj = self.signal.compute_trajectory(all_est_nodes)
        euclidean_dist = np.linalg.norm(est_traj[:,1:4] - opt_traj[:,1:4], axis=1)
        n_nodes = est_traj.shape[0]
        labels = [[0]] * n_nodes
        for i in range(0, n_nodes):
            if euclidean_dist[i] > 1.0:
                labels[i].append(1)
        return ClassificationResult(key, all_opt_nodes, euclidean_dist, labels)

    def perform_relative(self, key, all_opt_nodes, all_est_nodes):
        return self.set_label_for_all_nodes(1, key, all_opt_nodes, all_est_nodes)

    def perform_absolute(self, key, all_opt_nodes, all_est_nodes):
        n_all_nodes = len(all_opt_nodes)
        self.commander.send_anchors(all_opt_nodes, 0, n_all_nodes)
        return []
        # return self.set_label_for_all_nodes(5, key, all_opt_nodes, all_est_nodes)

    def set_label_for_all_nodes(self, label, key, all_opt_nodes, all_est_nodes):
        n_nodes = len(all_opt_nodes)
        labels = [[label]] * n_nodes
        return ClassificationResult(key, all_opt_nodes, None, labels)

    def evaluate_and_publish_features(self, labels):
        if labels == None or labels == [] or labels.size() == 0:
            return
        self.commander.evaluate_labels_per_node(labels)

    def key_in_optimized_keys(self, key):
       return any(key in k for k in self.optimized_keys)

    def key_in_keys(self, key):
       return any(key in k for k in self.keys)

if __name__ == '__main__':
    rospy.init_node('graph_client')
    node = GraphClient()
    while not rospy.is_shutdown():
        node.update()
        node.config.rate.sleep()
