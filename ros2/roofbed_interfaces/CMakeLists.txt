# SPDX-License-Identifier: GPL-3.0-or-later
cmake_minimum_required(VERSION 3.8)
project(roofbed_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(sensor_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "action/PlantCell.action"
  "action/WaterCells.action"
  "action/ProbeCells.action"
  "action/PhotoPass.action"
  "action/FeedWorms.action"
  DEPENDENCIES sensor_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
