#!/usr/bin/env bash

. /opt/ros/melodic/setup.bash
host_ip=$(ip route | awk '/default/ {print $3}')
export ROS_MASTER_URI=http://${host_ip}:11311
export ROS_IP=${host_ip}
exec "$@"
