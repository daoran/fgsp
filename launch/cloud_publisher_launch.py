import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    ld = LaunchDescription()

    config = os.path.join(
        get_package_share_directory('fgsp'),
        'config',
        'cloud_publisher_config.yaml'
    )

    publisher_node = Node(
        package="fgsp",
        executable="cloud_publisher",
        output={'both': 'screen'},
        emulate_tty=True,
        parameters=[config]
    )
    ld.add_action(publisher_node)

    return ld
