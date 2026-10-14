# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the roof-bed twin: Webots on a generated world, the gantry driver plugin, bed_actions and the gateway.

    ros2 launch roofbed_twin roofbed.launch.py out:=/path/with/roofbed.wbt/and/bed.json [gui:=true] [mode:=realtime] [port:=8773]

`out` is the folder permtek5_twin_edge/world.py wrote (roofbed.wbt, bed.json).
Webots is started directly on the world. Without the window it still renders
the robot's cameras. Everything stops when Webots exits.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess, OpaqueFunction, RegisterEventHandler,
                            SetEnvironmentVariable)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from webots_ros2_driver.webots_controller import WebotsController


def setup(context):
    out = os.path.abspath(LaunchConfiguration("out").perform(context))
    mode = LaunchConfiguration("mode").perform(context)
    gui = LaunchConfiguration("gui").perform(context).lower() == "true"
    world, bed = os.path.join(out, "roofbed.wbt"), os.path.join(out, "bed.json")
    for f in (world, bed):
        if not os.path.exists(f):
            raise RuntimeError(f"{f} is missing: generate the world first (permtek5-twin-edge world)")
    webots_port = LaunchConfiguration("webots_port").perform(context)
    cmd = ["webots", f"--mode={mode}", "--batch", f"--port={webots_port}", "--stdout", "--stderr"]   # its console into sim.log
    if not gui:
        cmd += ["--no-rendering", "--minimize"]
    webots = ExecuteProcess(cmd=cmd + [world], output="screen", name="webots")
    urdf = os.path.join(get_package_share_directory("roofbed_twin"), "resource", "gantry.urdf")
    return [
        SetEnvironmentVariable("PERMTEK5_TWIN_BED", bed),
        SetEnvironmentVariable("PERMTEK5_TWIN_OUT", out),
        SetEnvironmentVariable("PERMTEK5_TWIN_GATEWAY_PORT", LaunchConfiguration("port").perform(context)),
        webots,
        WebotsController(robot_name="gantry", port=webots_port, parameters=[{"robot_description": urdf}]),
        Node(package="roofbed_twin", executable="bed_actions", output="screen"),
        Node(package="roofbed_twin", executable="gateway", output="screen"),
        RegisterEventHandler(OnProcessExit(target_action=webots, on_exit=[EmitEvent(event=Shutdown())])),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("out", description="folder with roofbed.wbt and bed.json"),
        DeclareLaunchArgument("mode", default_value="realtime", description="Webots mode: realtime or fast"),
        DeclareLaunchArgument("gui", default_value="false", description="true: open the Webots window"),
        DeclareLaunchArgument("port", default_value="8773", description="the gateway's port on 127.0.0.1"),
        DeclareLaunchArgument("webots_port", default_value="1234", description="Webots' own port: one per guild on a computer"),
        OpaqueFunction(function=setup),
    ])
