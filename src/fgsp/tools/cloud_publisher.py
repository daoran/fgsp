#! /usr/bin/env python3

import os
import numpy as np

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import PointCloud2
from geometry_msgs.msg import PoseStamped
from sensor_msgs_py import point_cloud2
from nav_msgs.msg import Path
from std_msgs.msg import Header
import open3d as o3d


from src.fgsp.common.logger import Logger


class CloudPublisher(Node):
    def __init__(self):
        super().__init__('cloud_publisher')
        Logger.Verbosity = 5
        Logger.LogInfo('CloudPublisher: Initializing...')

        self.cloud_topic = self.get_param('cloud_topic', '/point_cloud')
        self.cloud_input_path = self.get_param('cloud_input_path', '')
        self.enable_clouds = True
        if (self.cloud_input_path == '' or not os.path.exists(self.cloud_input_path)):
            Logger.LogWarn(
                f'CloudPublisher: Invalid input path: {self.cloud_input_path}')
            self.enable_clouds = False

        self.path_topic = self.get_param('path_topic', '/path')
        self.path_input_path = self.get_param('path_input_path', '')
        self.enable_paths = True
        if (self.path_input_path == '' or not os.path.exists(self.path_input_path)):
            Logger.LogWarn(
                f'CloudPublisher: Invalid input path: {self.path_input_path}')
            self.enable_paths = False

        self.use_voxel_grid = self.get_param('use_voxel_grid', False)
        self.voxel_size = self.get_param('voxel_size', 0.1)

        if self.enable_clouds:
            self.clouds = self.read_clouds(self.cloud_input_path)
            self.n_clouds = len(self.clouds)
            if (self.n_clouds == 0):
                Logger.LogError('CloudPublisher: No clouds found')

            self.cloud_pubs = [None] * self.n_clouds
            for i in range(self.n_clouds):
                self.cloud_pubs[i] = self.create_publisher(
                    PointCloud2, f'{self.cloud_topic}_{i}', 10)

        if self.enable_paths:
            self.paths = self.read_paths(self.path_input_path)
            self.n_paths = len(self.paths)
            if (self.n_paths == 0):
                Logger.LogError('CloudPublisher: No poses found')

            self.path_pubs = [None] * self.n_paths
            for i in range(self.n_paths):
                self.path_pubs[i] = self.create_publisher(
                    Path, f'{self.path_topic}_{i}', 10)

        Logger.LogInfo(
            f'CloudPublisher: Read clouds from {self.cloud_input_path}')
        Logger.LogInfo(
            f'pathPublisher: Read paths from {self.path_input_path}')
        Logger.LogInfo(
            f'CloudPublisher: Publishing to {self.cloud_topic} and {self.path_topic}')
        Logger.LogInfo(
            'CloudPublisher: Initializing done. Publishing clouds...')

        rate = self.get_param('rate', 0.1)
        self.timer = self.create_timer(
            1 / rate, self.publish)

    def get_param(self, key, default):
        self.declare_parameter(key, default)
        return self.get_parameter(key).value

    def read_clouds(self, input_path):
        in_clouds = os.listdir(input_path)
        parsed_clouds = []
        if (len(in_clouds) == 0):
            Logger.LogError(
                f'CloudPublisher: No clouds found in {input_path}')
            return parsed_clouds
        for cloud_npy in in_clouds:
            path = f'{input_path}/{cloud_npy}'
            cloud = np.load(path)
            Logger.LogInfo(
                f'CloudPublisher: Read {cloud.shape} points from {cloud_npy}')
            parsed_clouds.append(cloud)

        return parsed_clouds

    def parse_path(self, path):
        path_msg = Path()
        for pose in path:
            t = pose[1:4]
            pose_msg = PoseStamped()
            pose_msg.header.stamp = self.get_clock().now().to_msg()
            pose_msg.pose.position.x = t[0]
            pose_msg.pose.position.y = t[1]
            pose_msg.pose.position.z = t[2]
            pose_msg.pose.orientation.x = 0.0
            pose_msg.pose.orientation.y = 0.0
            pose_msg.pose.orientation.z = 0.0
            pose_msg.pose.orientation.w = 1.0
            path_msg.poses.append(pose_msg)
        return path_msg

    def read_paths(self, input_path):
        in_paths = os.listdir(input_path)
        parsed_paths = []
        if (len(in_paths) == 0):
            Logger.LogError(
                f'CloudPublisher: No paths found in {input_path}')
        for path_npy in in_paths:
            full_path = f'{input_path}/{path_npy}'
            path = self.parse_path(np.load(full_path))
            Logger.LogInfo(
                f'CloudPublisher: Read {len(path.poses)} poses from {path_npy}')
            parsed_paths.append(path)
        return parsed_paths

    def voxel_down_sample(self, cloud, voxel_size=0.1):
        print(f'cloud: {cloud.shape}')
        pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(cloud))
        pcd = pcd.voxel_down_sample(voxel_size=voxel_size)
        return np.array(pcd.points)

    def publish(self):
        header = Header()
        header.stamp = self.get_clock().now().to_msg()
        header.frame_id = 'map'
        for i in range(self.n_clouds):
            cloud = self.clouds[i]
            if (self.use_voxel_grid):
                cloud = self.voxel_down_sample(cloud, self.voxel_size)
            msg = point_cloud2.create_cloud_xyz32(header, cloud)
            self.cloud_pubs[i].publish(msg)

        for i in range(self.n_paths):
            path = self.paths[i]
            path.header = header
            self.path_pubs[i].publish(path)

        Logger.LogInfo(
            f'Published {self.n_clouds} clouds and {self.n_paths} paths')


def main(args=None):
    rclpy.init(args=args)
    server = CloudPublisher()
    rclpy.spin(server)
    server.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
