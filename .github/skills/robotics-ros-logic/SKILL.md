---
name: robotics-ros-logic
description: >
  Specialist in ROS 2 node architecture, launch file composition, and
  ros_gz_bridge configuration. Activates when tasks involve ROS 2 package
  creation, topic/service/action patterns, tf2 transforms, nav2 integration,
  or colcon workspace management. Mandatory build command: colcon build.
---

# Robotics ROS Logic Engineer

> **Role**: Design and implement ROS 2 node architectures with proper lifecycle management.
> **Mandatory Command**: `colcon build --symlink-install`

## Core Competencies

### 1. ROS 2 Node Architecture

- **Lifecycle Nodes**: Use managed nodes (`rclcpp_lifecycle` / `rclcpp`) for predictable state transitions
- **Composition**: Prefer component nodes loaded into a single process for reduced latency
- **Executors**: Choose between `SingleThreadedExecutor`, `MultiThreadedExecutor`, and `StaticSingleThreadedExecutor`
- **QoS Profiles**: Match Quality of Service to data criticality:

| Data Type | QoS Profile | Reliability | History |
|-----------|-------------|-------------|---------|
| Sensor data | `SensorDataQoS` | Best effort | Keep last |
| Commands | `ServicesQoS` | Reliable | Keep all |
| TF | `StaticBroadcasterQoS` | Reliable | Transient local |
| Parameters | `ParametersQoS` | Reliable | Keep all |

### 2. Launch File Composition (Python Launch API)

```python
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='true'),
        
        Node(
            package='my_robot_pkg',
            executable='controller_node',
            name='controller',
            parameters=[{
                'use_sim_time': LaunchConfiguration('use_sim_time'),
            }],
            remappings=[('/cmd_vel', '/robot/cmd_vel')],
            output='screen',
        ),
    ])
```

- Use `IncludeLaunchDescription` for modular launch composition
- Always declare arguments with defaults for configurability
- Group related nodes with `GroupAction` and `PushRosNamespace`

### 3. ros_gz_bridge Configuration

Bridge ROS 2 topics to/from Gazebo transport:

```yaml
# bridge_config.yaml
- ros_topic_name: "/scan"
  gz_topic_name: "/lidar"
  ros_type_name: "sensor_msgs/msg/LaserScan"
  gz_type_name: "gz.msgs.LaserScan"
  direction: GZ_TO_ROS

- ros_topic_name: "/cmd_vel"
  gz_topic_name: "/model/robot/cmd_vel"
  ros_type_name: "geometry_msgs/msg/Twist"
  gz_type_name: "gz.msgs.Twist"
  direction: ROS_TO_GZ
```

- Map ALL sensor topics (lidar, camera, IMU) from Gazebo to ROS 2
- Map command topics (cmd_vel, joint commands) from ROS 2 to Gazebo
- Use `parameter_bridge` node with YAML config for maintainability

### 4. Communication Patterns

| Pattern | Use When | ROS 2 Construct |
|---------|----------|-----------------|
| **Pub/Sub** | Streaming data, fire-and-forget | `Publisher` / `Subscription` |
| **Service** | Request/response, quick operations | `Service` / `Client` |
| **Action** | Long-running tasks with feedback | `ActionServer` / `ActionClient` |
| **Parameters** | Runtime configuration | `ParameterService` |
| **Lifecycle** | Managed state transitions | `LifecycleNode` |

### 5. tf2 Transform Management

- Publish static transforms via `StaticTransformBroadcaster` for fixed frames
- Use `TransformBroadcaster` for dynamic frames (odom → base_link)
- Always define complete transform tree: `map → odom → base_link → sensor_frames`
- Validate with `ros2 run tf2_tools view_frames`

### 6. Navigation Stack (Nav2)

- Configure `nav2_bringup` with proper costmap parameters
- Set up behavior trees for robot navigation
- Tune DWB/MPPI controllers for platform-specific dynamics
- Integrate with SLAM (slam_toolbox) or localization (AMCL)

### 7. Colcon Workspace Management

```bash
# MANDATORY: All builds use colcon
colcon build --symlink-install --packages-select <pkg_name>
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
colcon test --packages-select <pkg_name>
colcon test-result --verbose

# Source workspace after build
source install/setup.bash
```

## Package Structure Convention

```
ros2_ws/src/
├── my_robot_description/     # URDF/SDF, meshes, rviz configs
├── my_robot_bringup/         # Launch files, config YAML
├── my_robot_control/         # Controllers, motion planning
├── my_robot_perception/      # Sensor processing, SLAM
├── my_robot_navigation/      # Nav2 config, behavior trees
├── my_robot_interfaces/      # Custom msg/srv/action definitions
└── my_robot_gazebo/          # Gazebo worlds, bridge configs
```

## Dependencies Declaration

Always declare dependencies in `package.xml`:

```xml
<depend>rclcpp</depend>
<depend>rclpy</depend>
<depend>std_msgs</depend>
<depend>geometry_msgs</depend>
<depend>sensor_msgs</depend>
<depend>nav_msgs</depend>
<depend>tf2_ros</depend>
<exec_depend>ros_gz_bridge</exec_depend>
```

And in `CMakeLists.txt`:
```cmake
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(geometry_msgs REQUIRED)
ament_target_dependencies(my_node rclcpp geometry_msgs)
```

## 2025 Modern Standard Mandates
- **REQUIRED**: Enforce the **Jazzy + Gazebo Harmonic** compatibility matrix. Emphasize `ros2_control`.

## Quality Checklist

- [ ] All packages build cleanly with `colcon build --symlink-install`
- [ ] `colcon test` passes with zero failures
- [ ] Launch files are parameterized (no hardcoded values)
- [ ] QoS profiles match data criticality
- [ ] Transform tree is complete and validated
- [ ] Bridge config covers all required Gazebo ↔ ROS 2 topics
- [ ] Custom interfaces have proper `.msg`/`.srv`/`.action` definitions
