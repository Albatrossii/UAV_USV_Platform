"""护航守卫与 GBSFLACS 三维多目标围捕仿真。

运行示例：

    python 护航守卫_GBSFLACS_三维多目标_Python39_10-9+自定义指令.py

依赖：numpy、scipy、scikit-learn、matplotlib。

初始化时没有敌方目标。按 S 随机生成 1 个目标；N 按当前设置生成 1 或 2 个目标；
也可输入 targets 1 / targets 2 立即重新生成。全编队固定为 3 UAV + 3 USV。
守卫及按 J 启动的围捕均为每目标最近三人；手动 bind 优先于最近分配。
仅一个目标被感知时，另外三人保留感知时的相对位置，跟随守卫方向旋转护航。
围捕成功只要求本目标至少三个成员在三维捕获半径内，无类型或角隙门槛。
数字 1-12 为全部 12 种单平台追击；13-18 绑定，19/G 守卫，20/J 围捕。
0/C/F4/cancel/retreat 随时撤退护航；最新规则详见 10-12_S单目标与旋转护航说明.md。
"""
# [修改 M01] 多目标说明与冗余清理；完整变更见同目录的 9-2_代码审查与修改说明.md。
# 修改标记格式：[修改 Mxx]；原文件备份：本文件名.before_review.bak。

# [新增 CMD01-CMD05] 按 J 前手动指定围捕成员；使用说明见 9-21_手动围捕指令说明.md。
# [历史 NUM01-NUM05] 数字指令与撤退基础；本轮 TASK01-TASK06 覆盖旧六人规则。
# [修改 TASK01-TASK06] 详见 10-08_三平台围捕与单机追击说明.md。
import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"  # 额外限制 MKL 专用线程
import argparse
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist
from sklearn.cluster import KMeans

# ---------------------------------------------------------------------------
# 用户可直接修改的编队参数
# ---------------------------------------------------------------------------
# [修改 TASK01] 总库存与目标数量解耦；目标数量只允许 1 或 2。
TARGETS_NUM = 2
NUM_UAV = 3
NUM_USV = 3
MIN_CAPTURE_AGENTS = 3

ESCORT_RESERVE_COUNT = 0

WORLD_X_MIN = -80.0
WORLD_X_MAX = 80.0
WORLD_Y_MIN = -50.0
WORLD_Y_MAX = 50.0
OWN_START_X = -52.0
OWN_START_Y = 0.0


SUPPORT_GUARD_RADIUS = 4.2
SUPPORT_ARC_HALF_ANGLE_DEG = 65.0
SUPPORT_ARC_REAR_OFFSET_DEG = 180.0

# ---------------------------------------------------------------------------
# 多目标分层守卫参数


# 每个被感知目标使用多少个平台进行前置守卫。
# 与后续 GBSFLACS 的三人小队总数保持一致，不限制 UAV/USV 比例。
GUARD_AGENTS_PER_TARGET = MIN_CAPTURE_AGENTS

# 不同目标守卫队形之间的径向层间距
GUARD_LAYER_GAP = 1.40

# 守卫阶段的平台避碰距离
GUARD_SAFE_DISTANCE = 1.10

# 守卫阶段斥力强度
GUARD_REPULSION_GAIN = 0.045
# ---------------------------------------------------------------------------


DEFAULT_SENSOR_RADIUS = 12.0

UAV_ALTITUDE_MIN = 0.0
UAV_ALTITUDE_MAX = 2.0
VIEW_Z_MIN = 0.0
VIEW_Z_MAX = 10.0

RANDOM_ORBIT_MIN_ANGLE_DEG = 25.0
RANDOM_ORBIT_MAX_ANGLE_DEG = 120.0
RANDOM_ORBIT_MIN_SPEED = 0.012
RANDOM_ORBIT_MAX_SPEED = 0.024
RANDOM_ORBIT_REVERSE_PROBABILITY = 0.65

WING_ARRIVAL_TOLERANCE = 0.35
WING_READY_RATIO = 0.80

DIS_GLOBAL = 1   # 是否显示俯视图


# [修改 LAYOUT02] 保留用户当前编号：1-12 追击、13-18 绑定、19 守卫、20 围捕。
# 1-9 可直接按；10-20 按 M 后输入编号并回车，避免两位数被当成两个快捷键。
PRESET_CAPTURE_COMMANDS = {
    # 单个控制
    "1": "chase U1 T1",     # 一号无人机追击一号敌船
    "2": "chase S1 T1",     # 一号无人艇追击一号敌船
    "3": "chase U2 T2",     # 二号无人机追击二号敌船
    "4": "chase S2 T2",     # 二号无人艇追击二号敌船
    "5": "chase U3 T1",     # 三号无人机追击一号敌船
    "6": "chase S3 T2",     # 三号无人艇追击二号敌船
    "7": "chase U1 T2",     # 一号无人机追击二号敌船
    "8": "chase S1 T2",     # 一号无人艇追击二号敌船
    "9": "chase U2 T1",     # 二号无人机追击一号敌船
    "10": "chase S2 T1",    # 二号无人艇追击一号敌船
    "11": "chase U3 T2",    # 三号无人机追击二号敌船
    "12": "chase S3 T1",    # 三号无人艇追击一号敌船
    # 同类型追击同一个目标
    "13": "bind T1 U1 U2 U3",   # 一、二、三号无人机追击一号敌船
    "14": "bind T2 S1 S2 S3",   # 一、二、三号无人艇追击二号敌船
    "15": "bind T1 S1 S2 S3",   # 一、二、三号无人艇追击一号敌船
    "16": "bind T2 U1 U2 U3",   # 一、二、三号无人机追击二号敌船
    # usv/UAV均包含
    "17": "bind T1 U1 S1 U2",   # 一、二号无人机和一号无人艇追击一号敌船
    "18": "bind T2 S2 U3 S3",   # 二、三号无人艇和三号无人机追击二号敌船
    # 守卫队形
    "19": "guard",      # 形成守卫队形
    # 围捕
    "20": "capture",    # 启动围捕算法
}


EPS = 1e-12
# [修复 TRIGGER01] forming 必须属于已感知状态；否则守卫下一帧被清空，G/J 均失效。
DETECTED_STATES = {"detected", "forming", "orbiting"}
STATE_LABELS = {
    "waiting": "等待出现",
    "approaching": "接近中",
    "detected": "已感知",
    "forming": "守卫编队中",
    "orbiting": "环绕机动",
    "captured": "GBSFLACS 围捕成功",
}

# ---------------------------------------------------------------------------
# GBSFLACS 物理尺度适配（算法流程保持原脚本实现）
# ---------------------------------------------------------------------------
SEED = 1234
ARENA_SIZE_XY = max(WORLD_X_MAX - WORLD_X_MIN, WORLD_Y_MAX - WORLD_Y_MIN)
CAPTURE_RADIUS = 2.6
GBSFLACS_CAPTURE_RADIUS = CAPTURE_RADIUS




TARGET_IS_STATIC = 0
TARGET_RUN_NUM = 30.0

# [修改 TASK03] 优化器按半径模式评价；成功判定固定为半径内至少三人。
# 仍保留近距离环形航点控制，角隙仅作诊断。
CAPTURE_MODE = "radius"  # [修改 TASK03] 成功判定仅看本目标三维半径内人数
MAX_CAPTURE_ANGULAR_GAP = np.pi
USE_TARGET_PREDICTION = False
SFLA_ACTIVATION_DISTANCE = 2.0 * CAPTURE_RADIUS
USE_BASE_ENCIRCLEMENT_CONTROLLER = True
BASE_RING_RADIUS_RATIO = 0.65
BASE_RING_UAV_HEIGHT_RATIO = 0.45
CS_STAGNATION_LIMIT = 2
CS_COOLDOWN = 3
MAX_ASSIGNMENT_SWITCH_RATIO = 0.15
SWITCH_MIN_ETA_GAIN = 0.25
SFLA_DEACTIVATION_DISTANCE = 1.20 * SFLA_ACTIVATION_DISTANCE
SFLA_EARLY_STOP_TOL = 1e-4
SFLA_EARLY_STOP_PATIENCE = 2
CS_QUALITY_TRIGGER = 0.45
QTH = 0.6
ALPHA = 0.4
BETA = 0.4
GAMMA = 0.2
V_MAX_UAV = 0.28
V_MAX_USV = 0.15
TARGET_SPEED = 0.30
USV_Z = 0.0
TARGET_Z = 0.0
CS_PA = 0.25
CS_BETA = 1.5


def horizontal(value: np.ndarray) -> np.ndarray:
    """Return the horizontal x/y components of a 2-D or 3-D vector."""
    arr = np.asarray(value, dtype=float)
    if arr.shape[0] < 2:
        raise ValueError("a position/vector must contain at least x and y")
    return arr[:2].copy()


def point3(value: np.ndarray, altitude: float = 0.0) -> np.ndarray:
    """Build a three-dimensional position from horizontal coordinates."""
    xy = horizontal(value)
    return np.array([xy[0], xy[1], float(altitude)], dtype=float)


def with_altitude(value: np.ndarray, altitude: float) -> np.ndarray:
    """Copy a position and force its z coordinate to the requested altitude."""
    return point3(value, altitude)


def horizontal_distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(horizontal(a) - horizontal(b)))


def normalize(v: np.ndarray, fallback: Optional[np.ndarray] = None) -> np.ndarray:
    arr = np.asarray(v, dtype=float)
    norm = float(np.linalg.norm(arr))
    if norm <= EPS:
        if fallback is None:
            return np.zeros_like(arr)
        fb = np.asarray(fallback, dtype=float)
        fb_norm = float(np.linalg.norm(fb))
        return fb / fb_norm if fb_norm > EPS else np.zeros_like(arr)
    return arr / norm


def rotate90(v: np.ndarray) -> np.ndarray:
    arr = np.asarray(v, dtype=float)
    return np.array([-arr[1], arr[0]], dtype=float)


# [修改 M01] 删除未调用的 wrapped_angle_distance。


def compute_blocker_point(
    own: np.ndarray,
    enemy: np.ndarray,
    ratio: float = 0.38,
    r_min: float = 2.2,
    r_max: float = 4.0,
    fallback_direction: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, float]:
    """计算严格位于敌我水平连线内部、海拔为 0 的动态核心阻断点。"""
    if not (0.0 < ratio < 1.0):
        raise ValueError("ratio must be in (0, 1)")
    if not (0.0 <= r_min <= r_max):
        raise ValueError("expected 0 <= r_min <= r_max")
    own_xy = horizontal(own)
    enemy_xy = horizontal(enemy)
    delta = enemy_xy - own_xy
    distance = float(np.linalg.norm(delta))
    if distance <= EPS:
        delta = normalize(
            np.array([1.0, 0.0]) if fallback_direction is None else horizontal(fallback_direction),
            np.array([1.0, 0.0]),
        )
        distance = 1.0
    requested = float(np.clip(ratio * distance, r_min, r_max))
    radius = min(max(distance * 1e-9, requested), distance * (1.0 - 1e-9))
    t = radius / distance
    return point3(own_xy + t * delta, 0.0), float(t)


@dataclass
class Platform:
    identifier: str
    kind: str
    position: np.ndarray
    max_speed: float
    gain: float
    altitude: float
    role: str = "escort"
    goal: Optional[np.ndarray] = None
    assigned_threat_id: Optional[int] = None


@dataclass
class ThreatTask:
    threat_id: int
    spawn_frame: int
    spawn_angle: float
    spawn_radius: float
    position: np.ndarray
    state: str = "waiting"
    current_speed_limit: float = 0.0
    detected_frame: Optional[int] = None
    blocker_point: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    blocker_t: float = math.nan
    guard_quota: int = 0
    core_guard_index: Optional[int] = None
    wing_guard_indices: List[int] = field(default_factory=list)
    wing_slot_by_index: Dict[int, int] = field(default_factory=dict)
    core_motion_state: str = "idle"
    core_dispatch_origin: Optional[np.ndarray] = None
    core_dispatch_initial_distance: float = 0.0
    core_trajectory: List[np.ndarray] = field(default_factory=list)
    controlled_radius: float = 0.0
    controlled_angle: float = 0.0
    orbit_direction: int = 1
    orbit_segment_angle: float = 0.0
    orbit_segment_remaining: float = 0.0
    orbit_angular_speed_current: float = 0.0
    orbit_segment_count: int = 0
    orbit_direction_changes: int = 0


class BaseAlgorithm(ABC):
    """所有对比算法的基类"""

    def __init__(self, env, name="Base"):
        self.env = env
        self.name = name
        # [修改 M01] 复用指标初始化，避免两份字典逐渐不一致。
        self.reset_metrics()

    @abstractmethod
    def step(self):
        """返回：{agent_id: target_id}"""
        pass

    def reset_metrics(self):
        """重置性能指标"""
        self.metrics = {
            "capture_time": [],
            "total_distance": 0,
            "recluster_count": 0,
            "reconfiguration_count": 0,
            "energy_consumption": 0,
            "target_load_balance": 0.0,
            "success_rate": 0
        }

    def _active_agents(self):
        """返回未被锁定为守卫的智能体，避免守卫继续参与分组和任务分配。"""
        guarding_ids = set()
        if hasattr(self.env, "_get_guarding_agent_ids"):
            guarding_ids = set(self.env._get_guarding_agent_ids())
        elif hasattr(self.env, "guarding_agents"):
            for guard_ids in self.env.guarding_agents.values():
                guarding_ids.update(int(agent_id) for agent_id in guard_ids)

        return [
            agent for agent in self.env.agents
            if int(agent[7]) not in guarding_ids
            and (getattr(self.env, "capture_agent_ids", None) is None
                 or int(agent[7]) in self.env.capture_agent_ids)
        ]



# [修改 TASK02] 统一最近分配：水平距离总和最小，每目标三人，禁止重复占用平台。
def nearest_team_assignment(agent_positions, target_positions, fixed=None, preferred=None):
    """先满足手动绑定，再用匈牙利匹配补足每目标三人；不限制类型比例。"""
    fixed = dict(fixed or {})
    preferred = dict(preferred or {})
    counts = {t: 0 for t in target_positions}
    for a, t in fixed.items():
        if a not in agent_positions or t not in target_positions:
            raise ValueError("固定分配包含不可用平台或目标")
        counts[t] += 1
        if counts[t] > MIN_CAPTURE_AGENTS:
            raise ValueError(f"每个目标最多 {MIN_CAPTURE_AGENTS} 个固定成员")
    free_agents = sorted(a for a in agent_positions if a not in fixed)
    slots = [t for t in sorted(target_positions)
             for _ in range(MIN_CAPTURE_AGENTS - counts[t])]
    if len(free_agents) < len(slots):
        raise ValueError("可用平台不足，无法为每个目标分配 3 个成员")
    result = dict(fixed)
    if slots:
        cost = np.array([[np.linalg.norm(np.asarray(agent_positions[a])[:2]
                                        - np.asarray(target_positions[t])[:2])
                          for t in slots] for a in free_agents])
        # 仅修复算法候选时优先少换组；初次派遣及守卫完全按最近距离。
        if preferred:
            penalty = (float(cost.max()) + 1.0) * (len(slots) + 1)
            cost += np.array([[0.0 if preferred.get(a) == t else penalty
                               for t in slots] for a in free_agents])
        rows, cols = linear_sum_assignment(cost)
        result.update({free_agents[int(r)]: slots[int(c)] for r, c in zip(rows, cols)})
    return result

def calculate_target_load_balance(assignments, active_target_ids, active_agent_ids=None):
    """Return the coefficient of variation of active-target assignment loads."""
    active_target_ids = list(active_target_ids)
    if not active_target_ids:
        return 0.0

    if active_agent_ids is None:
        active_agent_ids = set(assignments)
    else:
        active_agent_ids = set(active_agent_ids)

    target_to_index = {target_id: index for index, target_id in enumerate(active_target_ids)}
    loads = np.zeros(len(active_target_ids), dtype=float)
    for agent_id, target_id in assignments.items():
        if agent_id in active_agent_ids and target_id in target_to_index:
            loads[target_to_index[target_id]] += 1

    mean_load = np.mean(loads)
    if mean_load <= 1e-12:
        return 0.0
    return float(np.std(loads) / mean_load)


def count_reconfigurations(previous_assignments, current_assignments, active_agent_ids=None):
    """Count agents whose effective target assignment changed after initialization."""
    if not previous_assignments:
        return 0

    if active_agent_ids is None:
        active_agent_ids = set(current_assignments)
    else:
        active_agent_ids = set(active_agent_ids)

    return sum(
        agent_id in previous_assignments
        and previous_assignments[agent_id] != target_id
        for agent_id, target_id in current_assignments.items()
        if agent_id in active_agent_ids
    )


def calculate_success_rate(captured_target_ids, total_targets):
    """Return the fraction of targets captured in the current run."""
    if total_targets <= 0:
        return 0.0
    return len(captured_target_ids) / total_targets


# ==================== 算法1：你的GB-SFLA（基于现有代码重构） ====================
class GBSFLACSAlgorithm(BaseAlgorithm):
    """有效的粒球-SFLA-CS混合围捕算法。

    设计原则：
    1. 粒球仅保存由 ``env.agents`` 同步得到的真实状态；
    2. SFLA 优化可执行围捕航点，不修改真实坐标或粒球观测；
    3. CS 优化完整分配/结构候选，只有综合代价下降时才接受；
    4. 目标分配先满足最低围捕容量，再优化距离、负载与重配置。
    """

    class GranularBallNode:
        def __init__(self, ball_id, agent_ids=None, parent=None):
            self.ball_id = int(ball_id)
            self.agent_ids = list(agent_ids or [])
            self.parent = parent
            self.children = []
            self.children_quality = []
            self.is_leaf = True
            self.is_empty = False
            self.points = np.empty((0, 3), dtype=float)
            self.center = np.zeros(3, dtype=float)
            self.radius = 0.0
            self.density = 0.0
            self.quality = 0.0

        def sync_from_agent_map(self, agent_map):
            self.agent_ids = [
                int(agent_id) for agent_id in self.agent_ids
                if int(agent_id) in agent_map
            ]
            # [修改 M01] 成员同步和兼容接口共用一份几何计算。
            self.points = np.asarray(
                [agent_map[agent_id][:3] for agent_id in self.agent_ids], dtype=float
            ).reshape((-1, 3))
            self.update_features()

        def update_features(self):
            """兼容环境绘图/同步代码；只根据当前 points 更新几何属性。"""
            points = np.asarray(self.points, dtype=float)
            if points.size == 0:
                self.points = np.empty((0, 3), dtype=float)
                self.center = np.zeros(3, dtype=float)
                self.radius = 0.0
                self.density = 0.0
                self.is_empty = True
                return
            self.points = points.reshape((-1, 3))
            self.center = np.mean(self.points, axis=0)
            self.radius = float(np.max(np.linalg.norm(self.points - self.center, axis=1))) if len(self.points) > 1 else 0.0
            volume = (4.0 / 3.0) * np.pi * max(self.radius, 1e-6) ** 3
            self.density = float(len(self.points) / (volume + 1e-12))
            self.is_empty = False

    def __init__(self, env, name="GB-SFLA-CS", use_sfla=True, use_cs=True):
        super().__init__(env, name)
        # 四个粒球消融版本共享同一主流程，仅通过这两个开关移除模块。
        self.use_sfla = bool(use_sfla)
        self.use_cs = bool(use_cs)
        self.QTH = QTH  # [修改 M01] 使用唯一的粒球质量阈值配置。
        self.split_tolerance = 0.035
        self.merge_tolerance = 0.025
        self.accept_tolerance = 1e-9

        self.sfla_population_size = 8
        self.sfla_memeplex_count = 2
        self.sfla_local_iterations = 3

        self.cs_nests = 6
        self.cs_iterations = 2
        self.cs_pa = CS_PA
        self.cs_beta = CS_BETA

        self.weights = {
            "uncovered": 100.0,
            "completion": 30.0,
            "angular": 20.0,
            "makespan": 10.0,
            "distance": 4.0,
            "balance": 3.0,
            "reconfiguration": 2.0,
            "energy": 1.0,
            "group": 2.0,
        }

        self.rng = np.random.default_rng(int(getattr(env, "seed", SEED)) + 701)
        self.next_ball_id = 0
        self.leaf_balls = []
        self.ball_tree = None
        self.all_balls = {}
        self.desired_waypoints = {}
        self.last_assignments = {}
        self.last_objective = np.inf
        self._agent_map_cache = None
        self._target_reference_cache = {}
        self._speed_cache = {}
        # SFLA 跨时间步热启动与槽位连续性状态。
        self.previous_sfla_plans = {}
        self.previous_slot_waypoints = {}
        # 共享近距离围捕激活状态。保留 sfla_active_targets 作为兼容别名。
        self.encirclement_active_targets = set()
        self.sfla_active_targets = self.encirclement_active_targets
        self.sfla_slot_continuity_weight = 0.35
        self.sfla_early_stop_tolerance = SFLA_EARLY_STOP_TOL
        self.sfla_early_stop_patience = SFLA_EARLY_STOP_PATIENCE
        # CS 事件触发状态。
        self.cs_stagnation_steps = 0
        self.cs_cooldown_remaining = 0
        self.last_cs_priority = None
        self.last_active_target_signature = None
        self.last_cs_event = None
        self.metrics['use_sfla'] = int(self.use_sfla)
        self.metrics['use_cs'] = int(self.use_cs)
        self.metrics['sfla_waypoint_steps'] = 0
        self.metrics['cs_structure_accepts'] = 0
        self.metrics['cs_assignment_accepts'] = 0
        self.metrics['cs_trigger_count'] = 0
        self.metrics['cs_cooldown_skips'] = 0
        self.metrics['assignment_makespan'] = 0.0
        self.metrics['assignment_completion_gap'] = 0.0
        self.metrics['assignment_angular_gap'] = 0.0
        self.metrics['switch_budget_used'] = 0
        self.metrics['released_redundant_agents'] = 0
        self.metrics['cs_event_target_change'] = 0
        self.metrics['cs_event_constraint'] = 0
        self.metrics['cs_event_stagnation'] = 0
        self.metrics['cs_event_quality'] = 0
        self.metrics['sfla_warm_start_hits'] = 0
        self.metrics['sfla_early_stops'] = 0
        self.metrics['sfla_activation_count'] = 0
        self.metrics['base_encirclement_activation_count'] = 0
        self.metrics['base_encirclement_waypoint_steps'] = 0

    # ------------------------------------------------------------------
    # 基础状态和粒球几何
    # ------------------------------------------------------------------
    def _get_next_id(self):
        value = self.next_ball_id
        self.next_ball_id += 1
        return value

    def _active_target_ids(self):
        return [
            target_id for target_id in range(len(self.env.targets))
            if target_id not in self.env.permanently_captured
            # [修改 M02] targets 与 threats 保持同索引；等待出现的目标不参与分配。
            and (not hasattr(self.env, "threats")
                 or self.env.threats[target_id].state != "waiting")
        ]

    def _begin_step_cache(self):
        active_agents = BaseAlgorithm._active_agents(self)
        self._agent_map_cache = {int(agent[7]): agent for agent in active_agents}
        self._target_reference_cache = {}
        self._speed_cache = {
            int(agent[7]): float(V_MAX_UAV if int(agent[6]) == 0 else V_MAX_USV)
            for agent in active_agents
        }

    def _active_agent_map(self):
        if self._agent_map_cache is not None:
            return self._agent_map_cache
        return {int(agent[7]): agent for agent in BaseAlgorithm._active_agents(self)}

    def _max_speed(self, agent_id):
        agent_id = int(agent_id)
        if agent_id in self._speed_cache:
            return self._speed_cache[agent_id]
        agent = self._active_agent_map().get(agent_id)
        if agent is None:
            for candidate in self.env.agents:
                if int(candidate[7]) == int(agent_id):
                    agent = candidate
                    break
        if agent is None:
            return float(V_MAX_UAV)
        return float(V_MAX_UAV if int(agent[6]) == 0 else V_MAX_USV)

    def _new_ball(self, agent_ids, parent=None):
        ball = self.GranularBallNode(self._get_next_id(), agent_ids, parent=parent)
        ball.sync_from_agent_map(self._active_agent_map())
        ball.quality = self._calculate_cluster_quality(ball)
        return ball

    def _clone_partition(self, balls):
        return [self._new_ball(list(ball.agent_ids)) for ball in balls if ball.agent_ids]

    def _refresh_tree(self):
        self.all_balls = {ball.ball_id: ball for ball in self.leaf_balls}
        if not self.leaf_balls:
            self.ball_tree = None
        elif len(self.leaf_balls) == 1:
            self.ball_tree = self.leaf_balls[0]
            self.ball_tree.parent = None
        else:
            root_ids = [agent_id for ball in self.leaf_balls for agent_id in ball.agent_ids]
            root = self.GranularBallNode(-1, root_ids)
            root.sync_from_agent_map(self._active_agent_map())
            root.is_leaf = False
            root.children = self.leaf_balls
            root.children_quality = [float(ball.quality) for ball in self.leaf_balls]
            for ball in self.leaf_balls:
                ball.parent = root
                ball.is_leaf = True
            self.ball_tree = root
            self.all_balls[root.ball_id] = root
        self.env.ball_tree = self.ball_tree

    def _sync_real_balls(self):
        """用真实智能体位置更新粒球成员和几何，优化器不得改写真实状态。"""
        agent_map = self._active_agent_map()
        active_ids = sorted(agent_map)
        if not active_ids:
            self.leaf_balls = []
            self._refresh_tree()
            return

        if not self.leaf_balls:
            self.leaf_balls = [self._new_ball(active_ids)]
            self._refresh_tree()
            return

        # 先用上一步成员同步中心，再按最近真实中心重新归属。
        valid_balls = []
        for ball in self.leaf_balls:
            ball.agent_ids = [agent_id for agent_id in ball.agent_ids if agent_id in agent_map]
            ball.sync_from_agent_map(agent_map)
            if ball.agent_ids:
                valid_balls.append(ball)
        self.leaf_balls = valid_balls or [self._new_ball(active_ids)]

        centers = np.asarray([ball.center for ball in self.leaf_balls], dtype=float)
        memberships = {ball.ball_id: [] for ball in self.leaf_balls}
        for agent_id in active_ids:
            position = np.asarray(agent_map[agent_id][:3], dtype=float)
            nearest_index = int(np.argmin(np.linalg.norm(centers - position, axis=1)))
            memberships[self.leaf_balls[nearest_index].ball_id].append(agent_id)

        synced = []
        for ball in self.leaf_balls:
            ball.agent_ids = memberships[ball.ball_id]
            ball.sync_from_agent_map(agent_map)
            if ball.agent_ids:
                ball.quality = self._calculate_cluster_quality(ball)
                synced.append(ball)
        self.leaf_balls = synced or [self._new_ball(active_ids)]
        self._refresh_tree()

    def _calculate_cluster_quality(self, ball):
        n = len(ball.agent_ids)
        if n == 0:
            return 0.0
        if n == 1:
            return 0.25

        points = np.asarray(ball.points, dtype=float)
        avg_distance = float(np.mean(np.linalg.norm(points - ball.center, axis=1)))
        tightness = 1.0 / (1.0 + avg_distance / (ARENA_SIZE_XY * 0.1 + 1e-12))

        ideal_min = max(1, int(MIN_CAPTURE_AGENTS))
        ideal_max = ideal_min + 3
        if n < ideal_min:
            scale = n / ideal_min
        elif n <= ideal_max:
            scale = 1.0
        else:
            scale = max(0.35, 1.0 - 0.05 * (n - ideal_max))

        agent_map = self._active_agent_map()
        uav_count = sum(int(agent_map[agent_id][6]) == 0 for agent_id in ball.agent_ids if agent_id in agent_map)
        usv_count = n - uav_count
        if uav_count == 0 or usv_count == 0:
            heterogeneity = 0.5
        else:
            heterogeneity = 4.0 * uav_count * usv_count / (n * n)

        quality = ALPHA * tightness + BETA * scale + GAMMA * heterogeneity
        return float(np.clip(quality, 0.0, 1.0))

    def _ball_loss(self, ball):
        n = len(ball.agent_ids)
        min_agents = max(1, int(MIN_CAPTURE_AGENTS))
        shortage = max(0, min_agents - n) / min_agents
        oversize = max(0, n - (min_agents + 3)) / max(min_agents, 1)
        return float((1.0 - ball.quality) + 0.35 * shortage + 0.05 * oversize)

    def _partition_loss(self, balls, active_target_count):
        valid = [ball for ball in balls if ball.agent_ids]
        if not valid:
            return float("inf")
        total_agents = sum(len(ball.agent_ids) for ball in valid)
        weighted = sum(len(ball.agent_ids) * self._ball_loss(ball) for ball in valid) / max(total_agents, 1)
        feasible_group_count = max(1, min(active_target_count, total_agents // max(MIN_CAPTURE_AGENTS, 1)))
        count_gap = abs(len(valid) - feasible_group_count) / feasible_group_count
        undersized = sum(len(ball.agent_ids) < MIN_CAPTURE_AGENTS for ball in valid) / len(valid)
        return float(weighted + 0.12 * count_gap + 0.20 * undersized)

    # ------------------------------------------------------------------
    # 粒球分裂与合并：均在真实成员 ID 分区上进行
    # ------------------------------------------------------------------
    def _split_candidate(self, ball):
        minimum_child = max(2, int(MIN_CAPTURE_AGENTS))
        if len(ball.agent_ids) < 2 * minimum_child:
            return None
        if len(np.unique(np.round(ball.points, 8), axis=0)) < 2:
            return None

        labels = KMeans(n_clusters=2, random_state=SEED, n_init=10).fit_predict(ball.points)
        ids = np.asarray(ball.agent_ids, dtype=int)
        children_ids = [ids[labels == label].tolist() for label in (0, 1)]
        if any(len(group) < minimum_child for group in children_ids):
            return None
        return [self._new_ball(group) for group in children_ids]

    def _try_deterministic_split(self, ball, active_target_count):
        children = self._split_candidate(ball)
        if children is None:
            return None
        parent_loss = self._ball_loss(ball)
        child_loss = sum(len(child.agent_ids) * self._ball_loss(child) for child in children) / len(ball.agent_ids)
        capacity_pressure = len(self.leaf_balls) < active_target_count
        if child_loss + self.split_tolerance < parent_loss:
            return children
        if capacity_pressure and child_loss <= parent_loss + self.split_tolerance:
            return children
        return None

    def _try_merge_pair(self, balls, active_target_count):
        if len(balls) <= 1:
            return None
        candidates = []
        for i in range(len(balls)):
            for j in range(i + 1, len(balls)):
                a, b = balls[i], balls[j]
                distance = float(np.linalg.norm(a.center - b.center))
                candidates.append((distance, i, j))
        candidates.sort()

        current_loss = self._partition_loss(balls, active_target_count)
        for _, i, j in candidates:
            a, b = balls[i], balls[j]
            if len(a.agent_ids) >= MIN_CAPTURE_AGENTS and len(b.agent_ids) >= MIN_CAPTURE_AGENTS:
                continue
            merged = self._new_ball(a.agent_ids + b.agent_ids)
            proposal = [ball for index, ball in enumerate(balls) if index not in (i, j)] + [merged]
            proposal_loss = self._partition_loss(proposal, active_target_count)
            if proposal_loss + self.merge_tolerance < current_loss:
                return proposal
            if len(balls) > active_target_count and proposal_loss <= current_loss + self.merge_tolerance:
                return proposal
        return None

    def _adapt_structure(self, active_target_count):
        operations = 0
        max_operations = max(1, active_target_count)
        while operations < max_operations:
            candidates = sorted(
                self.leaf_balls,
                key=lambda ball: (ball.quality, -len(ball.agent_ids)),
            )
            accepted = False
            for ball in candidates:
                if ball.quality >= self.QTH and len(self.leaf_balls) >= active_target_count:
                    continue
                children = self._try_deterministic_split(ball, active_target_count)
                if children is None:
                    continue
                self.leaf_balls = [candidate for candidate in self.leaf_balls if candidate is not ball] + children
                self.metrics["recluster_count"] += 1
                operations += 1
                accepted = True
                break
            if not accepted or len(self.leaf_balls) >= active_target_count:
                break

        merged = self._try_merge_pair(self.leaf_balls, active_target_count)
        if merged is not None:
            self.leaf_balls = merged
            self.metrics["recluster_count"] += 1
        for ball in self.leaf_balls:
            ball.quality = self._calculate_cluster_quality(ball)
        self._refresh_tree()

    # ------------------------------------------------------------------
    # 目标参考位置、代价与容量优先分配
    # ------------------------------------------------------------------
    def _target_reference_position(self, target_id):
        """返回统一目标参考位置；预测开关独立于 SFLA/CS 消融开关。"""
        target_id = int(target_id)
        if target_id in self._target_reference_cache:
            return self._target_reference_cache[target_id].copy()
        target = self.env.targets[target_id]
        position = np.asarray(target[:3], dtype=float).copy()

        # 静态目标或关闭预测时，所有消融算法均使用完全相同的当前位置。
        if TARGET_IS_STATIC != 0 or not USE_TARGET_PREDICTION:
            position[2] = TARGET_Z
            self._target_reference_cache[target_id] = position.copy()
            return position

        nearby = []
        for agent in self.env.agents:
            distance_xy = float(np.linalg.norm(np.asarray(agent[:2]) - position[:2]))
            if distance_xy < TARGET_RUN_NUM:
                nearby.append(np.asarray(agent[:2], dtype=float))
        if nearby:
            mean_xy = np.mean(np.asarray(nearby), axis=0)
            direction = position[:2] - mean_xy
            norm = float(np.linalg.norm(direction))
            if norm > 1e-12:
                position[:2] += TARGET_SPEED * direction / norm
        else:
            speed = float(target[3]) if len(target) > 3 else float(TARGET_SPEED)
            theta = float(target[4]) if len(target) > 4 else 0.0
            position[:2] += speed * np.array([np.cos(theta), np.sin(theta)])
        position[2] = TARGET_Z
        self._target_reference_cache[target_id] = position.copy()
        return position

    def _arrival_cost(self, agent_id, target_id):
        agent = self._active_agent_map()[int(agent_id)]
        target_position = self._target_reference_position(int(target_id))
        return float(np.linalg.norm(np.asarray(agent[:3]) - target_position) / max(self._max_speed(agent_id), 1e-9))

    # [修改 TASK02] 三人总配额取代旧的冗余上限和类型配额。
    def _capacity_requirements(self, active_ids, active_target_ids):
        targets = [int(t) for t in active_target_ids]
        required_total = MIN_CAPTURE_AGENTS * len(targets)
        if len(active_ids) < required_total:
            raise RuntimeError(f"可用 {len(active_ids)} 个平台，{len(targets)} 个目标需要 {required_total} 个")
        return {t: MIN_CAPTURE_AGENTS for t in targets}

    def _capacity_aware_assignment(self, active_target_ids, balls=None):
        # [修改 TASK02] 初始派遣按实际距离，不用平台速度改变“最近”的含义。
        agents = self._active_agent_map()
        return nearest_team_assignment(
            {a: v[:3] for a, v in agents.items()},
            {int(t): self._target_reference_position(int(t)) for t in active_target_ids},
            fixed=self._fixed_assignments(active_target_ids),
        )

    # [新增 CMD02] 手动固定关系是硬约束，所有候选修复、评估及稳定器共用。
    def _fixed_assignments(self, active_target_ids):
        getter = getattr(self.env, "fixed_capture_assignments", None)
        if getter is None:
            return {}
        active_agents = self._active_agent_map()
        return {int(a): int(t) for a, t in getter().items()
                if int(a) in active_agents and int(t) in active_target_ids}

    # [修改 TASK02] 删除旧的六人和 UAV/USV 类型配额，统一为每目标三人。
    def _repair_assignment_capacity(self, assignments, active_target_ids):
        targets = [int(t) for t in active_target_ids]
        if not targets:
            return {}
        agents = self._active_agent_map()
        if len(agents) != MIN_CAPTURE_AGENTS * len(targets):
            raise ValueError("参战平台数必须等于活跃目标数 × 3")
        return nearest_team_assignment(
            {a: v[:3] for a, v in agents.items()},
            {t: self._target_reference_position(t) for t in targets},
            fixed=self._fixed_assignments(targets), preferred=assignments,
        )

    def _has_exact_quota(self, assignments, active_target_ids):
        return (set(assignments) == set(self._active_agent_map())
                and all(t in active_target_ids for t in assignments.values())
                and all(assignments.get(a) == t
                        for a, t in self._fixed_assignments(active_target_ids).items())
                and all(sum(t == target for t in assignments.values()) == MIN_CAPTURE_AGENTS
                        for target in active_target_ids))

    def _stabilize_assignment(self, candidate, active_target_ids):
        """保持每目标三人，按完整交换环限制非必要换组数量。"""
        active_target_ids = [int(t) for t in active_target_ids]
        candidate = self._repair_assignment_capacity(candidate, active_target_ids)
        agent_map = self._active_agent_map()
        previous = {int(a): int(t) for a, t in self.last_assignments.items()
                    if int(a) in agent_map and int(t) in active_target_ids}
        if not self._has_exact_quota(previous, active_target_ids):
            # 初始分配或目标集合变化时，硬配额可行性优先于可选换组预算。
            self.metrics['switch_budget_used'] = sum(
                a in previous and previous[a] != t for a, t in candidate.items()
            )
            return candidate

        stabilized = dict(previous)
        cycles = []
        # [修改 TASK02] 任意类型都能互换，须按总人数分解交换环。
        pending = {a for a in candidate if candidate[a] != previous[a]}
        # 两份可行方案的每目标入度等于出度，差异必能分解为有向环。
        while pending:
            node = previous[min(pending)]
            visited = {}
            edges = []
            while node not in visited:
                visited[node] = len(edges)
                a = min(a for a in pending if previous[a] == node)
                edges.append(a)
                node = candidate[a]
            cycle = edges[visited[node]:]
            pending.difference_update(cycle)
            gain = sum(self._arrival_cost(a, previous[a])
                       - self._arrival_cost(a, candidate[a]) for a in cycle)
            cycles.append((gain / len(cycle), tuple(cycle)))

        budget = max(0, int(np.ceil(MAX_ASSIGNMENT_SWITCH_RATIO * len(agent_map))))
        used = 0
        for gain, cycle in sorted(cycles, reverse=True):
            if gain < SWITCH_MIN_ETA_GAIN or used + len(cycle) > budget:
                continue
            for a in cycle:
                stabilized[a] = candidate[a]
            used += len(cycle)
        self.metrics['switch_budget_used'] = used
        return stabilized

    def _assignment_components(self, assignments, balls=None):
        """Evaluate capacity, current completion, geometry and predicted finish time."""
        active_target_ids = self._active_target_ids()
        active_agent_map = self._active_agent_map()
        active_ids = sorted(active_agent_map)
        invalid = {
            "uncovered": float("inf"), "completion": float("inf"),
            "angular": float("inf"), "makespan": float("inf"),
            "distance": float("inf"), "balance": float("inf"),
            "reconfiguration": float("inf"), "energy": float("inf"),
            "group": float("inf"),
        }
        if not active_target_ids:
            return {key: 0.0 for key in invalid}
        if set(assignments) != set(active_ids):
            return invalid
        if any(target_id not in active_target_ids for target_id in assignments.values()):
            return invalid
        # [新增 CMD02] 违反固定关系的方案不可被 CS 接受。
        if any(assignments.get(a) != t
               for a, t in self._fixed_assignments(active_target_ids).items()):
            return invalid

        requirements = self._capacity_requirements(active_ids, active_target_ids)
        loads = {target_id: 0 for target_id in active_target_ids}
        arrivals_by_target = {target_id: [] for target_id in active_target_ids}
        in_range_by_target = {target_id: [] for target_id in active_target_ids}
        arrival_times = []
        energy_terms = []
        for agent_id, target_id in assignments.items():
            target_id = int(target_id)
            loads[target_id] += 1
            arrival = self._arrival_cost(agent_id, target_id)
            arrivals_by_target[target_id].append(arrival)
            arrival_times.append(arrival)
            agent = active_agent_map[agent_id]
            target = self._target_reference_position(target_id)
            delta = np.asarray(agent[:3], dtype=float) - target
            distance = float(np.linalg.norm(delta))
            if distance <= CAPTURE_RADIUS:
                in_range_by_target[target_id].append(int(agent_id))
            energy_terms.append((distance / max(ARENA_SIZE_XY, 1)) ** 2)

        uncovered = float(sum(max(0, requirements[t] - loads[t]) for t in active_target_ids))
        completion = float(sum(max(0, requirements[t] - len(in_range_by_target[t])) for t in active_target_ids))
        angular = 0.0
        if CAPTURE_MODE == 'geometric':
            for target_id in active_target_ids:
                ids = in_range_by_target[target_id]
                if len(ids) < requirements[target_id]:
                    angular += 1.0
                    continue
                vectors = np.asarray([
                    active_agent_map[agent_id][:2] - self._target_reference_position(target_id)[:2]
                    for agent_id in ids
                ], dtype=float)
                valid = np.linalg.norm(vectors, axis=1) > 1e-9
                vectors = vectors[valid]
                if len(vectors) < 2:
                    angular += 1.0
                    continue
                angles = np.sort(np.mod(np.arctan2(vectors[:, 1], vectors[:, 0]), 2 * np.pi))
                gap = float(np.max(np.diff(np.r_[angles, angles[0] + 2 * np.pi])))
                angular += max(0.0, gap - MAX_CAPTURE_ANGULAR_GAP) / (2 * np.pi)

        # [修改 M04] 速度小于 1 时仍按真实速度归一化，不能强行截成 1。
        horizon = max(ARENA_SIZE_XY / max(min(V_MAX_UAV, V_MAX_USV), 1e-9), 1e-9)
        completion_times = []
        for target_id in active_target_ids:
            requirement = max(1, int(requirements[target_id]))
            arrivals = sorted(arrivals_by_target[target_id])
            if len(arrivals) >= requirement:
                completion_times.append(arrivals[requirement - 1])
            else:
                completion_times.append(horizon + float(requirement - len(arrivals)))
        makespan = float(max(completion_times, default=0.0) / horizon)
        distance_norm = float(np.mean(arrival_times) / horizon) if arrival_times else 0.0
        load_values = np.asarray(list(loads.values()), dtype=float)
        balance = float(np.std(load_values) / (np.mean(load_values) + 1e-12))
        previous = self.last_assignments or getattr(self.env, "previous_assignments", {}) or {}
        reconfiguration = sum(
            agent_id in previous and int(previous[agent_id]) != int(target_id)
            for agent_id, target_id in assignments.items()
        ) / max(len(assignments), 1)
        energy = float(np.mean(energy_terms)) if energy_terms else 0.0
        valid_balls = [ball for ball in (balls if balls is not None else self.leaf_balls) if ball.agent_ids]
        group_loss = float(np.mean([1.0 - ball.quality for ball in valid_balls])) if valid_balls else 1.0
        return {
            "uncovered": uncovered,
            "completion": completion,
            "angular": float(angular),
            "makespan": makespan,
            "distance": distance_norm,
            "balance": balance,
            "reconfiguration": float(reconfiguration),
            "energy": energy,
            "group": group_loss,
        }

    def _assignment_priority(self, assignments, balls=None):
        """Lexicographic objective: feasibility, real completion, geometry, finish time."""
        c = self._assignment_components(assignments, balls)
        return (
            c["uncovered"],
            c["completion"],
            c["angular"],
            c["makespan"],
            c["distance"],
            c["reconfiguration"],
            c["balance"],
            c["energy"],
            c["group"],
        )

    def _is_priority_better(self, candidate_priority, incumbent_priority):
        """按字典序比较，避免次要指标改善牺牲任务可行性或 makespan。"""
        for candidate, incumbent in zip(candidate_priority, incumbent_priority):
            candidate = float(candidate)
            incumbent = float(incumbent)
            if candidate + self.accept_tolerance < incumbent:
                return True
            if incumbent + self.accept_tolerance < candidate:
                return False
        return False

    def _composite_cost(self, assignments, balls=None):
        """保留标量值用于日志；CS接受决策使用 `_assignment_priority`。"""
        components = self._assignment_components(assignments, balls)
        if not all(np.isfinite(value) for value in components.values()):
            return float("inf")
        return float(sum(self.weights[key] * components[key] for key in self.weights))

    def _accept_assignment_if_better(self, incumbent, candidate, balls=None):
        incumbent_priority = self._assignment_priority(incumbent, balls)
        candidate_priority = self._assignment_priority(candidate, balls)
        if self._is_priority_better(candidate_priority, incumbent_priority):
            return True, dict(candidate), candidate_priority
        return False, dict(incumbent), incumbent_priority

    def _should_run_cs(self, current_priority, active_target_ids=None):
        """Run CS only for explicit state events, not on every simulation step."""
        if not self.use_cs:
            return False

        explicit_target_event = active_target_ids is not None
        if active_target_ids is None:
            active_target_ids = self._active_target_ids()
        else:
            active_target_ids = [int(value) for value in active_target_ids]

        signature = tuple(sorted(active_target_ids))
        target_changed = (
            explicit_target_event
            and self.last_active_target_signature is not None
            and signature != self.last_active_target_signature
        )
        if explicit_target_event:
            self.last_active_target_signature = signature

        has_constraint_gap = bool(
            current_priority and float(current_priority[0]) > self.accept_tolerance
        )
        poor_structure = any(
            ball.agent_ids and float(ball.quality) < CS_QUALITY_TRIGGER
            for ball in self.leaf_balls
        )
        stagnated = self.cs_stagnation_steps >= CS_STAGNATION_LIMIT
        initial_step = not bool(self.last_assignments)

        # Cooldown blocks ordinary quality/stagnation/constraint events.  A real
        # active-target set change bypasses cooldown because the old assignment
        # is no longer the same task instance.
        if self.cs_cooldown_remaining > 0 and not target_changed and not initial_step:
            self.metrics['cs_cooldown_skips'] += 1
            return False

        events = []
        if target_changed:
            self.metrics['cs_event_target_change'] += 1
            events.append('target_change')
        if has_constraint_gap:
            self.metrics['cs_event_constraint'] += 1
            events.append('constraint')
        if stagnated:
            self.metrics['cs_event_stagnation'] += 1
            events.append('stagnation')
        if poor_structure:
            self.metrics['cs_event_quality'] += 1
            events.append('quality')
        if initial_step:
            events.append('initial')
        self.last_cs_event = '+'.join(events) if events else None
        return bool(initial_step or target_changed or has_constraint_gap or stagnated or poor_structure)

    def _update_cs_progress(self, current_priority):
        if self.last_cs_priority is None or self._is_priority_better(current_priority, self.last_cs_priority):
            self.cs_stagnation_steps = 0
        else:
            self.cs_stagnation_steps += 1
        self.last_cs_priority = tuple(float(value) for value in current_priority)

    def _levy_flight(self, beta=None):
        beta = float(self.cs_beta if beta is None else beta)
        sigma_u = (
            math.gamma(1 + beta) * math.sin(math.pi * beta / 2)
            / (math.gamma((1 + beta) / 2) * beta * 2 ** ((beta - 1) / 2))
        ) ** (1 / beta)
        u = self.rng.normal(0, sigma_u)
        v = self.rng.normal(0, 1)
        return float(u / (abs(v) ** (1 / beta) + 1e-12))

    # [修改 M01] 删除未调用的标量接受器；CS 统一使用字典序接受条件。
    def _mutate_assignment(self, assignment, active_target_ids, balls=None):
        candidate = dict(assignment)
        active_target_ids = [int(target_id) for target_id in active_target_ids]
        if len(active_target_ids) <= 1 or not candidate:
            return candidate

        balls = balls if balls is not None else self.leaf_balls
        levy = abs(self._levy_flight())
        mutation_fraction = min(0.40, max(1.0 / len(candidate), 0.04 * levy))
        mutation_count = max(1, int(np.ceil(mutation_fraction * len(candidate))))

        fixed = self._fixed_assignments(active_target_ids)
        selected_ids = []
        if balls and self.rng.random() < 0.65:
            ball = balls[int(self.rng.integers(0, len(balls)))]
            selected_ids.extend(a for a in ball.agent_ids if a not in fixed)
        remaining = [agent_id for agent_id in candidate
                     if agent_id not in selected_ids and agent_id not in fixed]
        if len(selected_ids) < mutation_count and remaining:
            extra_count = min(mutation_count - len(selected_ids), len(remaining))
            selected_ids.extend(self.rng.choice(remaining, size=extra_count, replace=False).tolist())

        for agent_id in selected_ids[:max(mutation_count, len(selected_ids))]:
            current_target = candidate[agent_id]
            alternatives = [target_id for target_id in active_target_ids if target_id != current_target]
            if not alternatives:
                continue
            if self.rng.random() < 0.65:
                candidate[agent_id] = min(alternatives, key=lambda target_id: self._arrival_cost(agent_id, target_id))
            else:
                candidate[agent_id] = int(self.rng.choice(alternatives))
        return self._repair_assignment_capacity(candidate, active_target_ids)

    def _try_cs_structure_improvement(self, assignment, active_target_ids):
        incumbent_balls = self.leaf_balls
        incumbent_assignment = dict(assignment)
        incumbent_priority = self._assignment_priority(incumbent_assignment, incumbent_balls)

        for _ in range(max(2, self.cs_iterations)):
            proposal = self._clone_partition(incumbent_balls)
            if not proposal:
                break
            if self.rng.random() < 0.65:
                splittable = [ball for ball in proposal if len(ball.agent_ids) >= 2 * max(2, MIN_CAPTURE_AGENTS)]
                if not splittable:
                    continue
                chosen = min(splittable, key=lambda ball: ball.quality)
                children = self._split_candidate(chosen)
                if children is None:
                    continue
                proposal = [ball for ball in proposal if ball is not chosen] + children
            else:
                merged = self._try_merge_pair(proposal, len(active_target_ids))
                if merged is None:
                    continue
                proposal = merged

            proposal_assignment = self._capacity_aware_assignment(active_target_ids, proposal)
            proposal_priority = self._assignment_priority(proposal_assignment, proposal)
            if self._is_priority_better(proposal_priority, incumbent_priority):
                incumbent_balls = proposal
                incumbent_assignment = dict(proposal_assignment)
                incumbent_priority = proposal_priority
                self.metrics["recluster_count"] += 1
                self.metrics['cs_structure_accepts'] += 1

        self.leaf_balls = incumbent_balls
        self._refresh_tree()
        return incumbent_assignment, self._composite_cost(incumbent_assignment, incumbent_balls)

    def _try_cs_assignment_improvement(self, assignment, active_target_ids):
        incumbent = dict(assignment)
        incumbent_priority = self._assignment_priority(incumbent, self.leaf_balls)
        nests = [dict(incumbent)]
        for _ in range(self.cs_nests - 1):
            nests.append(self._mutate_assignment(incumbent, active_target_ids, self.leaf_balls))

        for _ in range(self.cs_iterations):
            updated = []
            for nest in nests:
                candidate = self._mutate_assignment(nest, active_target_ids, self.leaf_balls)
                accepted, chosen, _ = self._accept_assignment_if_better(nest, candidate, self.leaf_balls)
                updated.append(chosen if accepted else dict(nest))
            nests = updated

            priorities = [self._assignment_priority(nest, self.leaf_balls) for nest in nests]
            abandon_count = max(1, int(np.ceil(self.cs_pa * len(nests))))
            worst_indices = sorted(range(len(nests)), key=lambda index: priorities[index], reverse=True)[:abandon_count]
            for index in worst_indices:
                if self.rng.random() < self.cs_pa:
                    nests[index] = self._mutate_assignment(incumbent, active_target_ids, self.leaf_balls)

            best_nest = min(nests, key=lambda nest: self._assignment_priority(nest, self.leaf_balls))
            best_priority = self._assignment_priority(best_nest, self.leaf_balls)
            if self._is_priority_better(best_priority, incumbent_priority):
                incumbent = dict(best_nest)
                incumbent_priority = best_priority
                self.metrics['cs_assignment_accepts'] += 1
        return incumbent, self._composite_cost(incumbent, self.leaf_balls)

    def _plan_bounds_clip(self, params):
        clipped = np.asarray(params, dtype=float).copy()
        clipped[0] = clipped[0] % (2 * np.pi)
        clipped[1] = np.clip(clipped[1], 0.45, 0.72)
        clipped[2] = np.clip(clipped[2], 0.25, 0.75)
        return clipped

    def _encirclement_should_activate(self, target_id, critical_distance):
        """共享基础围捕控制器的近距离激活/退出滞回。"""
        target_id = int(target_id)
        if not USE_BASE_ENCIRCLEMENT_CONTROLLER:
            self.encirclement_active_targets.discard(target_id)
            return False

        was_active = target_id in self.encirclement_active_targets
        threshold = SFLA_DEACTIVATION_DISTANCE if was_active else SFLA_ACTIVATION_DISTANCE
        active = float(critical_distance) <= float(threshold)
        if active and not was_active:
            self.encirclement_active_targets.add(target_id)
            self.metrics['base_encirclement_activation_count'] += 1
            if self.use_sfla:
                self.metrics['sfla_activation_count'] += 1
        elif not active:
            self.encirclement_active_targets.discard(target_id)
        return active

    def _sfla_should_activate(self, target_id, critical_distance):
        """兼容旧接口；SFLA与基础控制器共享相同的近距离激活状态。"""
        return self._encirclement_should_activate(target_id, critical_distance)

    def _base_plan_parameters(self, agent_ids, target_id):
        """构造确定性的基础环形方案 ``[phase, radius_ratio, height_ratio]``。"""
        agent_ids = [int(agent_id) for agent_id in agent_ids]
        target_id = int(target_id)
        target_pos = self._target_reference_position(target_id)
        agent_map = self._active_agent_map()
        positions = np.asarray(
            [np.asarray(agent_map[agent_id][:3], dtype=float) for agent_id in agent_ids],
            dtype=float,
        )
        if len(positions):
            centroid = np.mean(positions[:, :2], axis=0)
            direction = centroid - target_pos[:2]
            if float(np.linalg.norm(direction)) > 1e-9:
                phase = float(np.mod(np.arctan2(direction[1], direction[0]), 2 * np.pi))
            else:
                phase = float(self.previous_sfla_plans.get(target_id, np.array([0.0]))[0])
        else:
            phase = 0.0
        return self._plan_bounds_clip(np.array([
            phase,
            BASE_RING_RADIUS_RATIO,
            BASE_RING_UAV_HEIGHT_RATIO,
        ], dtype=float))

    def _build_base_encirclement_waypoints(self, assignments, store_history=True):
        """为所有 GB 消融算法生成共享的远追击/近环形基础航点。"""
        active_agent_ids = {int(agent_id) for agent_id in assignments}
        active_target_ids = {int(target_id) for target_id in assignments.values()}
        self.previous_slot_waypoints = {
            int(agent_id): np.asarray(waypoint, dtype=float)
            for agent_id, waypoint in self.previous_slot_waypoints.items()
            if int(agent_id) in active_agent_ids
        }
        self.encirclement_active_targets.intersection_update(active_target_ids)

        grouped = {}
        for agent_id, target_id in assignments.items():
            grouped.setdefault(int(target_id), []).append(int(agent_id))

        waypoints = {}
        agent_map = self._active_agent_map()
        for target_id, agent_ids in grouped.items():
            target_ref = self._target_reference_position(target_id)
            distances = sorted(
                float(np.linalg.norm(np.asarray(agent_map[agent_id][:3]) - target_ref))
                for agent_id in agent_ids
            )
            required = min(max(1, int(MIN_CAPTURE_AGENTS)), len(distances))
            critical_distance = distances[required - 1] if distances else float('inf')

            if not self._encirclement_should_activate(target_id, critical_distance):
                for agent_id in agent_ids:
                    waypoint = np.asarray(target_ref, dtype=float).copy()
                    if int(agent_map[agent_id][6]) == 1:
                        waypoint[2] = USV_Z
                    waypoints[int(agent_id)] = waypoint
                continue

            base_params = self._base_plan_parameters(agent_ids, target_id)
            waypoints.update(
                self._plan_to_waypoints(
                    agent_ids, target_id, base_params, store_history=False
                )
            )
            self.metrics['base_encirclement_waypoint_steps'] += 1

        if store_history:
            for agent_id, waypoint in waypoints.items():
                self.previous_slot_waypoints[int(agent_id)] = np.asarray(waypoint, dtype=float).copy()
        return waypoints

    def _plan_to_waypoints(self, agent_ids, target_id, params, store_history=False):
        agent_ids = [int(agent_id) for agent_id in agent_ids]
        if len(agent_ids) != MIN_CAPTURE_AGENTS:
            raise ValueError(
                "encirclement requires exactly three platforms of any UAV/USV mix"
            )
        if len(set(agent_ids)) != MIN_CAPTURE_AGENTS:
            raise ValueError(
                "alternating encirclement requires three unique platform IDs"
            )
        agent_map = self._active_agent_map()
        unknown_ids = sorted(
            agent_id for agent_id in agent_ids if agent_id not in agent_map
        )
        if unknown_ids:
            unknown_text = ", ".join(str(agent_id) for agent_id in unknown_ids)
            raise ValueError(
                f"alternating encirclement received unknown platform IDs: "
                f"{unknown_text}"
            )

        params = self._plan_bounds_clip(params)
        target_pos = self._target_reference_position(target_id)
        ring_radius = float(params[1] * CAPTURE_RADIUS)
        uav_ids = [
            agent_id for agent_id in agent_ids
            if int(agent_map[agent_id][6]) == 0
        ]
        usv_ids = [
            agent_id for agent_id in agent_ids
            if int(agent_map[agent_id][6]) == 1
        ]
        # [修改 TASK03] 三个环位按实际类型组合分配，支持 3+0、2+1、1+2、0+3。
        if len(uav_ids) + len(usv_ids) != MIN_CAPTURE_AGENTS:
            raise ValueError("capture members must be UAV or USV")
        uav_slots = (np.floor(np.arange(len(uav_ids)) * MIN_CAPTURE_AGENTS / len(uav_ids)).astype(int)
                     if uav_ids else np.array([], dtype=int))
        usv_slots = np.array([slot for slot in range(MIN_CAPTURE_AGENTS) if slot not in uav_slots], dtype=int)
        angles = params[0] + 2.0 * np.pi * np.arange(MIN_CAPTURE_AGENTS) / float(MIN_CAPTURE_AGENTS)
        max_vertical = np.sqrt(
            max(CAPTURE_RADIUS ** 2 - ring_radius ** 2, 0.0)
        )
        uav_z = target_pos[2] + float(params[2] * 0.80 * max_vertical)
        slot_altitudes = np.full(MIN_CAPTURE_AGENTS, USV_Z, dtype=float)
        slot_altitudes[uav_slots] = uav_z
        slots = np.column_stack([
            target_pos[0] + ring_radius * np.cos(angles),
            target_pos[1] + ring_radius * np.sin(angles),
            slot_altitudes,
        ])

        waypoints = {}
        for typed_agent_ids, slot_indices in (
            (uav_ids, uav_slots),
            (usv_ids, usv_slots),
        ):
            if not typed_agent_ids:
                continue
            candidate_slots = slots[slot_indices]
            cost_matrix = np.zeros((len(typed_agent_ids), len(slot_indices)), dtype=float)
            for row, agent_id in enumerate(typed_agent_ids):
                agent = agent_map[agent_id]
                arrival_cost = (
                    np.linalg.norm(
                        candidate_slots - np.asarray(agent[:3]), axis=1
                    )
                    / max(self._max_speed(agent_id), 1e-9)
                )
                previous_slot = self.previous_slot_waypoints.get(agent_id)
                if previous_slot is not None:
                    continuity_cost = (
                        np.linalg.norm(
                            candidate_slots - np.asarray(previous_slot), axis=1
                        )
                        / max(CAPTURE_RADIUS, 1e-9)
                    )
                    arrival_cost = (
                        arrival_cost
                        + self.sfla_slot_continuity_weight * continuity_cost
                    )
                cost_matrix[row] = arrival_cost

            row_ind, col_ind = linear_sum_assignment(cost_matrix)
            for row, col in zip(row_ind, col_ind):
                agent_id = typed_agent_ids[int(row)]
                waypoint = np.asarray(
                    candidate_slots[int(col)], dtype=float
                ).copy()
                delta = waypoint - target_pos
                distance = float(np.linalg.norm(delta))
                if distance > CAPTURE_RADIUS:
                    waypoint = target_pos + delta * (
                        (CAPTURE_RADIUS * 0.98) / max(distance, 1e-12)
                    )
                if int(agent_map[agent_id][6]) == 1:
                    waypoint[2] = USV_Z
                waypoints[agent_id] = waypoint

        if store_history:
            for agent_id, waypoint in waypoints.items():
                self.previous_slot_waypoints[int(agent_id)] = np.asarray(waypoint, dtype=float).copy()
        return waypoints

    def _local_plan_cost(self, agent_ids, target_id, params):
        waypoints = self._plan_to_waypoints(agent_ids, target_id, params, store_history=False)
        if not waypoints:
            return float("inf")
        agent_map = self._active_agent_map()
        target_pos = self._target_reference_position(target_id)
        arrivals = []
        angles = []
        radii = []
        positions = []
        continuity_terms = []
        for agent_id, waypoint in waypoints.items():
            arrivals.append(np.linalg.norm(waypoint - np.asarray(agent_map[agent_id][:3])) / max(self._max_speed(agent_id), 1e-9))
            vector = waypoint[:2] - target_pos[:2]
            angles.append(float(np.mod(np.arctan2(vector[1], vector[0]), 2 * np.pi)))
            radii.append(float(np.linalg.norm(waypoint - target_pos)))
            positions.append(waypoint)
            previous_slot = self.previous_slot_waypoints.get(int(agent_id))
            if previous_slot is not None:
                continuity_terms.append(float(np.linalg.norm(waypoint - previous_slot) / max(CAPTURE_RADIUS, 1e-9)))

        sorted_angles = np.sort(np.asarray(angles))
        gaps = np.diff(np.r_[sorted_angles, sorted_angles[0] + 2 * np.pi]) if len(sorted_angles) > 1 else np.array([2 * np.pi])
        ideal_gap = 2 * np.pi / max(len(sorted_angles), 1)
        angular_error = float(np.max(np.abs(gaps - ideal_gap)) / (2 * np.pi))
        radius_error = float(np.mean(np.abs(np.asarray(radii) - np.mean(radii))) / max(CAPTURE_RADIUS, 1))

        collision_penalty = 0.0
        positions = np.asarray(positions)
        if len(positions) > 1:
            pairwise = cdist(positions, positions)
            pairwise += np.eye(len(positions)) * 1e9
            minimum_spacing = CAPTURE_RADIUS * 0.25
            collision_penalty = float(np.mean(np.maximum(0.0, minimum_spacing - pairwise) / minimum_spacing))

        arrival_norm = float(np.mean(arrivals) / max(ARENA_SIZE_XY / V_MAX_UAV, 1e-9))
        slot_continuity = float(np.mean(continuity_terms)) if continuity_terms else 0.0
        return float(
            4.0 * arrival_norm
            + 2.0 * angular_error
            + radius_error
            + 2.0 * collision_penalty
            + self.sfla_slot_continuity_weight * slot_continuity
        )

    def _sfla_target_waypoints(self, agent_ids, target_id, base_params=None):
        if not agent_ids:
            return {}
        target_id = int(target_id)
        base_candidate = self._plan_bounds_clip(
            self._base_plan_parameters(agent_ids, target_id)
            if base_params is None else np.asarray(base_params, dtype=float)
        )
        has_warm_start = target_id in self.previous_sfla_plans
        if has_warm_start:
            self.metrics['sfla_warm_start_hits'] += 1

        # 基础方案始终作为一只青蛙进入种群，确保 SFLA 在同一控制基线上优化。
        population = [base_candidate.copy()]
        if has_warm_start and self.sfla_population_size > 1:
            warm_start = self._plan_bounds_clip(
                np.asarray(self.previous_sfla_plans[target_id], dtype=float)
            )
            if not np.allclose(warm_start, base_candidate):
                population.append(warm_start)
        while len(population) < self.sfla_population_size:
            params = np.array([
                self.rng.uniform(0, 2 * np.pi),
                self.rng.uniform(0.45, 0.72),
                self.rng.uniform(0.25, 0.75),
            ])
            population.append(self._plan_bounds_clip(params))

        best_seen = float('inf')
        no_improvement = 0
        for _ in range(self.sfla_local_iterations):
            costs = np.asarray([
                self._local_plan_cost(agent_ids, target_id, params)
                for params in population
            ])
            order = np.argsort(costs)
            current_best = float(costs[int(order[0])])
            if best_seen - current_best > self.sfla_early_stop_tolerance:
                best_seen = current_best
                no_improvement = 0
            else:
                no_improvement += 1
                if no_improvement >= self.sfla_early_stop_patience:
                    self.metrics['sfla_early_stops'] += 1
                    break

            global_best = population[int(order[0])].copy()
            memeplexes = [
                order[index::self.sfla_memeplex_count].tolist()
                for index in range(self.sfla_memeplex_count)
            ]
            for memeplex in memeplexes:
                if len(memeplex) < 2:
                    continue
                local_best_index = min(memeplex, key=lambda index: costs[index])
                worst_index = max(memeplex, key=lambda index: costs[index])
                worst = population[worst_index]
                local_best = population[local_best_index]

                candidate = self._plan_bounds_clip(
                    worst + self.rng.random(3) * (local_best - worst)
                )
                candidate_cost = self._local_plan_cost(agent_ids, target_id, candidate)
                if candidate_cost >= costs[worst_index]:
                    candidate = self._plan_bounds_clip(
                        worst + self.rng.random(3) * (global_best - worst)
                    )
                    candidate_cost = self._local_plan_cost(agent_ids, target_id, candidate)
                if candidate_cost >= costs[worst_index]:
                    candidate = self._plan_bounds_clip(np.array([
                        self.rng.uniform(0, 2 * np.pi),
                        self.rng.uniform(0.45, 0.72),
                        self.rng.uniform(0.25, 0.75),
                    ]))
                    candidate_cost = self._local_plan_cost(agent_ids, target_id, candidate)
                if candidate_cost < costs[worst_index]:
                    population[worst_index] = candidate

        best = min(
            population,
            key=lambda params: self._local_plan_cost(agent_ids, target_id, params),
        )
        self.previous_sfla_plans[target_id] = np.asarray(best, dtype=float).copy()
        return self._plan_to_waypoints(agent_ids, target_id, best, store_history=True)

    def _build_sfla_waypoints(self, assignments, base_waypoints=None):
        """在共享基础航点上，仅对已进入近距离的目标执行 SFLA 优化。"""
        if not self.use_sfla:
            return dict(base_waypoints or {})

        active_agent_ids = {int(agent_id) for agent_id in assignments}
        active_target_ids = {int(target_id) for target_id in assignments.values()}
        self.previous_slot_waypoints = {
            int(agent_id): np.asarray(waypoint, dtype=float)
            for agent_id, waypoint in self.previous_slot_waypoints.items()
            if int(agent_id) in active_agent_ids
        }
        self.previous_sfla_plans = {
            int(target_id): np.asarray(plan, dtype=float)
            for target_id, plan in self.previous_sfla_plans.items()
            if int(target_id) in active_target_ids
        }
        self.encirclement_active_targets.intersection_update(active_target_ids)

        grouped = {}
        for agent_id, target_id in assignments.items():
            grouped.setdefault(int(target_id), []).append(int(agent_id))

        waypoints = dict(base_waypoints or {})
        agent_map = self._active_agent_map()
        for target_id, agent_ids in grouped.items():
            target_ref = self._target_reference_position(target_id)
            distances = sorted(
                float(np.linalg.norm(np.asarray(agent_map[agent_id][:3]) - target_ref))
                for agent_id in agent_ids
            )
            required = min(max(1, int(MIN_CAPTURE_AGENTS)), len(distances))
            critical_distance = distances[required - 1] if distances else float('inf')

            if not self._encirclement_should_activate(target_id, critical_distance):
                continue

            base_params = self._base_plan_parameters(agent_ids, target_id)
            waypoints.update(
                self._sfla_target_waypoints(
                    agent_ids, target_id, base_params=base_params
                )
            )
            self.metrics['sfla_waypoint_steps'] += 1

        for agent_id, waypoint in waypoints.items():
            self.previous_slot_waypoints[int(agent_id)] = np.asarray(waypoint, dtype=float).copy()
        return waypoints

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def _get_all_leaves(self, node):
        if node is None:
            return []
        if node.is_leaf:
            return [node]
        leaves = []
        for child in node.children:
            leaves.extend(self._get_all_leaves(child))
        return leaves

    def step(self):
        self._begin_step_cache()
        active_target_ids = self._active_target_ids()
        if not active_target_ids or not self._active_agent_map():
            self.desired_waypoints = {}
            return {}

        if self.cs_cooldown_remaining > 0:
            self.cs_cooldown_remaining -= 1

        self._sync_real_balls()
        self._adapt_structure(len(active_target_ids))

        assignment = self._capacity_aware_assignment(active_target_ids, self.leaf_balls)
        base_priority = self._assignment_priority(assignment, self.leaf_balls)

        if self._should_run_cs(base_priority, active_target_ids):
            self.metrics['cs_trigger_count'] += 1
            assignment, _ = self._try_cs_structure_improvement(assignment, active_target_ids)
            assignment, _ = self._try_cs_assignment_improvement(assignment, active_target_ids)
            self.cs_cooldown_remaining = CS_COOLDOWN

        # [修改 M03] 稳定器内部已执行配额修复，避免重复求解。
        assignment = self._stabilize_assignment(assignment, active_target_ids)
        final_priority = self._assignment_priority(assignment, self.leaf_balls)
        final_components = self._assignment_components(assignment, self.leaf_balls)
        objective = self._composite_cost(assignment, self.leaf_balls)
        self._update_cs_progress(final_priority)

        base_waypoints = self._build_base_encirclement_waypoints(
            assignment, store_history=not self.use_sfla
        )
        self.desired_waypoints = (
            self._build_sfla_waypoints(assignment, base_waypoints=base_waypoints)
            if self.use_sfla
            else base_waypoints
        )
        # [修改 M14] 将实际分配写入统计，避免负载与重配置指标始终为零。
        self.metrics['reconfiguration_count'] += count_reconfigurations(
            self.last_assignments, assignment, self._active_agent_map()
        )
        self.metrics['target_load_balance'] = calculate_target_load_balance(
            assignment, active_target_ids, self._active_agent_map()
        )
        self.last_assignments = dict(assignment)
        self.last_objective = float(objective)
        self.metrics['assignment_makespan'] = float(final_components['makespan'])
        self.metrics['assignment_completion_gap'] = float(final_components['completion'])
        self.metrics['assignment_angular_gap'] = float(final_components['angular'])
        self._refresh_tree()

        if bool(getattr(self.env, "gbsflacs_verbose", False)):
            print(
                f"   Assignments ({self.name}, SFLA={self.use_sfla}, CS={self.use_cs}): "
                f"{assignment}; balls={len(self.leaf_balls)}, objective={self.last_objective:.4f}, "
                f"makespan={final_components['makespan']:.4f}, "
                f"completion_gap={final_components['completion']:.1f}"
            )
        return assignment


class EscortGuardSimulator:
    """多个移动威胁的感知、分层守卫和三维围捕控制器。"""

    def __init__(
        self,
        *,
        sensor_radius: float = DEFAULT_SENSOR_RADIUS,
        seed: int = 42,
        num_uav: int = NUM_UAV,
        num_usv: int = NUM_USV,
        target_count: int = TARGETS_NUM,  # [修改 TASK01] 本实例的目标数量
        escort_reserve_count: int = ESCORT_RESERVE_COUNT,
        ring_radius: float = 4.0,
        guard_arc_radius: float = 5.2,
        support_guard_radius: float = SUPPORT_GUARD_RADIUS,
        support_arc_half_angle_deg: float = SUPPORT_ARC_HALF_ANGLE_DEG,
        support_arc_rear_offset_deg: float = SUPPORT_ARC_REAR_OFFSET_DEG,
        guard_arc_half_angle_deg: float = 32.0,
        max_guard_arc_half_angle_deg: float = 75.0,
        minimum_guard_spacing: float = 1.05,
        blocker_ratio: float = 0.38,
        blocker_r_min: float = 2.2,
        blocker_r_max: float = 4.0,
        core_arrival_tolerance: float = 0.24,
        wing_arrival_tolerance: float = WING_ARRIVAL_TOLERANCE,
        wing_ready_ratio: float = WING_READY_RATIO,
        enemy_approach_speed: float = 0.10,
        enemy_forming_speed: float = 0.14,
        enemy_controlled_speed: float = 0.30,
        enemy_min_radius: float = 6.6,
        random_orbit_min_angle_deg: float = RANDOM_ORBIT_MIN_ANGLE_DEG,
        random_orbit_max_angle_deg: float = RANDOM_ORBIT_MAX_ANGLE_DEG,
        random_orbit_min_speed: float = RANDOM_ORBIT_MIN_SPEED,
        random_orbit_max_speed: float = RANDOM_ORBIT_MAX_SPEED,
        random_orbit_reverse_probability: float = RANDOM_ORBIT_REVERSE_PROBABILITY,
        avoidance_mode: str = "auto",
        avoid_distance: float = 3.8,
        forward_shift: float = 2.2,
        own_max_speed: float = 0.075,
        own_gain: float = 0.08,
        cruise_speed: float = 0.035,
        safe_distance: float = GUARD_SAFE_DISTANCE,
        repulsion_gain: float = GUARD_REPULSION_GAIN,
        own_target_avoid_radius: float = 2.0,
        core_trail_length: int = 240,
        dt: float = 1.0,
    ) -> None:
        # [修改 M05] 拒绝会导致逆向运动、停滞或 NaN 传播的时间步与避碰参数。
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and positive")
        if not math.isfinite(safe_distance) or safe_distance <= 0:
            raise ValueError("safe_distance must be finite and positive")
        if not math.isfinite(repulsion_gain) or repulsion_gain < 0:
            raise ValueError("repulsion_gain must be finite and non-negative")
        # [修改 TASK01] 固定库存；拒绝隐式截断小数或无效目标数量。
        if num_uav != NUM_UAV or num_usv != NUM_USV:
            raise ValueError("总编队固定为 3 UAV + 3 USV")
        if isinstance(target_count, bool) or target_count not in (1, 2):
            raise ValueError("目标数量只能为 1 或 2")
        if escort_reserve_count not in {0, 2}:
            raise ValueError("escort_reserve_count must be 0 or 2")
        if support_guard_radius <= own_target_avoid_radius:
            raise ValueError("support_guard_radius must exceed the own-target safety radius")
        if not (0.0 <= support_arc_half_angle_deg < 180.0):
            raise ValueError("support_arc_half_angle_deg must be in [0, 180)")
        if sensor_radius <= max(guard_arc_radius, support_guard_radius, own_target_avoid_radius):
            raise ValueError("sensor_radius must exceed guard and safety radii")
        if not (0.0 < wing_ready_ratio <= 1.0):
            raise ValueError("wing_ready_ratio must be in (0, 1]")
        if not (0.0 < random_orbit_min_angle_deg <= random_orbit_max_angle_deg <= 360.0):
            raise ValueError("random orbit angles must satisfy 0 < min <= max <= 360")
        if not (0.0 < random_orbit_min_speed <= random_orbit_max_speed):
            raise ValueError("random orbit speeds must satisfy 0 < min <= max")
        if not (0.0 <= random_orbit_reverse_probability <= 1.0):
            raise ValueError("random_orbit_reverse_probability must be in [0, 1]")

        self.seed = int(seed)
        self.reset_count = 0
        self.rng = np.random.default_rng(self.seed)
        self.num_uav = int(num_uav)
        self.num_usv = int(num_usv)
        self.target_count = int(target_count)
        self.spawn_mode = "multiple_random_directions"
        self.enemy_motion = "random_segment_orbit"
        self.sensor_radius = float(sensor_radius)
        self.escort_reserve_count = int(escort_reserve_count)
        self.ring_radius = float(ring_radius)
        self.guard_arc_radius = float(guard_arc_radius)
        self.support_guard_radius = float(support_guard_radius)
        self.support_arc_half_angle = math.radians(float(support_arc_half_angle_deg))
        self.support_arc_rear_offset = math.radians(float(support_arc_rear_offset_deg))
        self.guard_arc_half_angle = math.radians(float(guard_arc_half_angle_deg))
        self.max_guard_arc_half_angle = math.radians(float(max_guard_arc_half_angle_deg))
        self.minimum_guard_spacing = float(minimum_guard_spacing)
        self.blocker_ratio = float(blocker_ratio)
        self.blocker_r_min = float(blocker_r_min)
        self.blocker_r_max = float(blocker_r_max)
        self.core_arrival_tolerance = float(core_arrival_tolerance)
        self.wing_arrival_tolerance = float(wing_arrival_tolerance)
        self.wing_ready_ratio = float(wing_ready_ratio)
        self.enemy_approach_speed = float(enemy_approach_speed)
        self.enemy_forming_speed = float(enemy_forming_speed)
        self.enemy_controlled_speed = float(enemy_controlled_speed)
        self.enemy_min_radius = float(enemy_min_radius)
        self.random_orbit_min_angle = math.radians(float(random_orbit_min_angle_deg))
        self.random_orbit_max_angle = math.radians(float(random_orbit_max_angle_deg))
        self.random_orbit_min_speed = float(random_orbit_min_speed)
        self.random_orbit_max_speed = float(random_orbit_max_speed)
        self.random_orbit_reverse_probability = float(random_orbit_reverse_probability)
        self.avoidance_mode = avoidance_mode
        self.avoid_distance = float(avoid_distance)
        self.forward_shift = float(forward_shift)
        self.own_max_speed = float(own_max_speed)
        self.own_gain = float(own_gain)
        self.cruise_speed = float(cruise_speed)
        self.safe_distance = float(safe_distance)
        self.repulsion_gain = float(repulsion_gain)
        self.own_target_avoid_radius = float(own_target_avoid_radius)
        self._own_target_route_margin = max(0.08, 0.03 * self.own_target_avoid_radius)
        self.core_trail_length = int(core_trail_length)
        self.dt = float(dt)
        self.world_x_min = float(WORLD_X_MIN)
        self.world_x_max = float(WORLD_X_MAX)
        self.world_y_min = float(WORLD_Y_MIN)
        self.world_y_max = float(WORLD_Y_MAX)
        self.initial_own_position = np.array([OWN_START_X, OWN_START_Y, 0.0], dtype=float)
        if not (
            self.world_x_min + self.ring_radius < self.initial_own_position[0] < self.world_x_max - self.ring_radius
            and self.world_y_min + self.ring_radius < self.initial_own_position[1] < self.world_y_max - self.ring_radius
        ):
            raise ValueError("The initial escort position must leave room for the escort ring")

        self.forward = np.array([1.0, 0.0], dtype=float)
        self.own_position = self.initial_own_position.copy()
        self.own_goal = self.own_position.copy()
        self.avoid_direction = np.zeros(2, dtype=float)
        self.frame = 0
        self.paused = False
        self.phase = "正常护航"

        self.last_message = "当前没有敌方目标，按 S 生成单目标，或按 N 按当前数量生成"

        self.platforms = self._create_mixed_ring()

        # 初始化时没有敌方目标，按 N 键后再随机生成
        self.threats: List[ThreatTask] = []

        self.reserve_guard_indices: List[int] = []
        self._reserve_slot_offsets: Dict[int, np.ndarray] = {}
        self._reserve_slot_by_index: Dict[int, int] = {}
        self.support_guard_indices: List[int] = []
        self._support_slot_by_index: Dict[int, int] = {}
        self._last_detected_ids: Tuple[int, ...] = ()
        self._clear_rotating_escort()
        self._own_target_bypass_side: Dict[int, int] = {}
        self.gbsflacs_active = False
        self.capture_success = False
        self.capture_start_frame: Optional[int] = None
        self.escort_only = False  # [新增 NUM04] 取消后锁定护航，防止感知触发重新布防。
        self.gbsflacs_verbose = False
        self.permanently_captured: set = set()
        self.guarding_agents: Dict[int, set] = {}
        # [修改 M06] 独立保存每个已捕获小队的最终航点，不受下一帧优化器覆盖。
        self.capture_final_waypoints: Dict[int, np.ndarray] = {}
        self.previous_assignments: Dict[int, int] = {}
        # [新增 CMD01] 手动固定关系仅约束围捕阶段，N 键重置时清除。
        self.manual_capture_assignments: Dict[int, int] = {}
        # [新增 TASK04] 追击目标用显示编号，围捕参战名单用平台内部索引。
        self.pursuit_assignments: Dict[int, int] = {}
        self.capture_agent_ids: Optional[set] = None
        self.ball_tree = None
        self.agents = np.empty((0, 8), dtype=float)
        self.targets = np.empty((0, 6), dtype=float)
        self.last_gbsflacs_assignments: Dict[int, int] = {}
        self.gbsflacs_algorithm = GBSFLACSAlgorithm(self)
        self._sync_gbsflacs_environment()

    @property
    def max_targets(self) -> int:
        return self.target_count

    @property
    def threat_count(self) -> int:
        return len(self.threats)

    @property
    def spawned_threats(self) -> List[ThreatTask]:
        return [task for task in self.threats if task.state != "waiting"]

    @property
    def detected_threats(self) -> List[ThreatTask]:
        return [task for task in self.threats if task.state in DETECTED_STATES]

    @property
    def threat_active(self) -> bool:
        return bool(self.detected_threats)

    @property
    def forward_guard_indices(self) -> List[int]:
        """所有已分配的普通守卫，不再区分核心守卫。"""
        result: List[int] = []

        for task in self.detected_threats:
            result.extend(task.wing_guard_indices)

        return result

    def get_threat(self, threat_id: int) -> ThreatTask:
        for task in self.threats:
            if task.threat_id == threat_id:
                return task
        raise KeyError(f"Unknown threat id {threat_id}")

    def _create_mixed_ring(self) -> List[Platform]:
        kinds: List[str] = []
        uav_left, usv_left = self.num_uav, self.num_usv
        prefer_uav = True
        while uav_left + usv_left:
            if (prefer_uav and uav_left > 0) or usv_left == 0:
                kinds.append("UAV")
                uav_left -= 1
            else:
                kinds.append("USV")
                usv_left -= 1
            prefer_uav = not prefer_uav
        rotation = float(self.rng.uniform(-0.10, 0.10))
        angles = np.linspace(0.0, 2.0 * math.pi, len(kinds), endpoint=False) + rotation
        result: List[Platform] = []
        uav_no = usv_no = 0
        for angle, kind in zip(angles, kinds):
            horizontal_position = horizontal(self.own_position) + self.ring_radius * np.array(
                [math.cos(angle), math.sin(angle)], dtype=float
            )
            if kind == "UAV":
                uav_no += 1
                altitude = float(self.rng.uniform(UAV_ALTITUDE_MIN, UAV_ALTITUDE_MAX))
                position = point3(horizontal_position, altitude)
                result.append(Platform(f"U{uav_no}", kind, position, 0.28, 0.42, altitude))
            else:
                usv_no += 1
                altitude = 0.0
                position = point3(horizontal_position, altitude)
                result.append(Platform(f"S{usv_no}", kind, position, 0.15, 0.32, altitude))
        return result

    def _create_threat_schedule(self) -> List[ThreatTask]:
        """按当前 target_count 随机生成 1 或 2 个敌方目标。"""

        tasks = []

        for target_id in range(self.target_count):
            # [修改 M15] 在合法世界内生成目标，避免首次裁剪造成超速跳跃。
            center = horizontal(self.own_position)
            lower = np.array([self.world_x_min + 0.2, self.world_y_min + 0.2])
            upper = np.array([self.world_x_max - 0.2, self.world_y_max - 0.2])
            for _ in range(128):
                angle = float(self.rng.uniform(0.0, 2.0 * math.pi))
                radius = float(self.rng.uniform(self.sensor_radius + 4.0, self.sensor_radius + 8.0))
                xy = center + radius * np.array([math.cos(angle), math.sin(angle)])
                if np.all(xy >= lower) and np.all(xy <= upper):
                    break
            else:
                # 有限次随机采样后，用最远角点方向提供确定性回退。
                corners = np.array([[x, y] for x in (lower[0], upper[0])
                                    for y in (lower[1], upper[1])])
                corner = max(corners, key=lambda p: np.linalg.norm(p - center))
                distance = float(np.linalg.norm(corner - center))
                if distance < self.sensor_radius + 4.0:
                    raise ValueError("world has no room to spawn outside the sensor radius")
                radius = min(radius, distance)
                direction = (corner - center) / distance
                angle = math.atan2(direction[1], direction[0])
                xy = center + radius * direction
            position = point3(xy)

            tasks.append(
                ThreatTask(
                    threat_id=target_id + 1,
                    spawn_frame=self.frame,
                    spawn_angle=angle,
                    spawn_radius=radius,
                    position=position,
                    state="approaching",
                    current_speed_limit=self.enemy_approach_speed,
                )
            )

        return tasks

    def _start_random_orbit_segment(self, task: ThreatTask) -> None:
        """Choose the next random angular segment and clockwise/counterclockwise direction."""
        previous_direction = task.orbit_direction
        if task.orbit_segment_count == 0:
            new_direction = int(self.rng.choice(np.array([-1, 1], dtype=int)))
        elif float(self.rng.random()) < self.random_orbit_reverse_probability:
            new_direction = -previous_direction
        else:
            new_direction = previous_direction
        if task.orbit_segment_count > 0 and new_direction != previous_direction:
            task.orbit_direction_changes += 1
        task.orbit_direction = new_direction
        task.orbit_segment_angle = float(
            self.rng.uniform(self.random_orbit_min_angle, self.random_orbit_max_angle)
        )
        task.orbit_segment_remaining = task.orbit_segment_angle
        task.orbit_angular_speed_current = float(
            self.rng.uniform(self.random_orbit_min_speed, self.random_orbit_max_speed)
        )
        task.orbit_segment_count += 1

    def reset(self) -> None:
        """按 N 生成新目标；保留我方目标、平台位置和 UAV 当前高度。"""
        self.reset_count += 1
        self.escort_only = False
        self.gbsflacs_active = False
        self.capture_success = False
        for platform in self.platforms:
            platform.role = "escort"
            platform.goal = platform.position.copy()
            platform.assigned_threat_id = None
            if platform.kind == "UAV":
                platform.altitude = float(platform.position[2])
            else:
                platform.altitude = 0.0
                platform.position[2] = 0.0

        self.reserve_guard_indices = []
        self._reserve_slot_offsets = {}
        self._reserve_slot_by_index = {}
        self.support_guard_indices = []
        self._support_slot_by_index = {}
        self._last_detected_ids = ()
        self._own_target_bypass_side = {}
        self.avoid_direction = np.zeros(2, dtype=float)

        # [修改 M07] 复用唯一的目标生成入口，保留当前帧和当前我方位置。
        self.threats = self._create_threat_schedule()
        self.phase = "正常护航"
        self.last_message = (
            f"已随机生成 {self.target_count} 个敌方目标；"
            "我方编队位置与 UAV 当前高度保持不变"
        )
        self._reset_gbsflacs_runtime()


    # [新增 CMD01] 保存“平台内部索引 -> 显示目标编号”，与护航角色分开管理。
    def _validate_manual_capture_assignments(self, assignments: Dict[int, int]) -> None:
        tasks = {task.threat_id: task for task in self.threats}
        counts: Dict[int, int] = {}
        for index, threat_id in assignments.items():
            if not 0 <= index < len(self.platforms):
                raise ValueError(f"未知平台索引：{index}")
            if threat_id not in tasks or tasks[threat_id].state == "waiting":
                raise ValueError(f"目标 T{threat_id} 尚未生成")
            # [新增 NUM02] 不限制单一类型数量，只限制每目标合计最多 3 个。
            counts[threat_id] = counts.get(threat_id, 0) + 1
            if counts[threat_id] > MIN_CAPTURE_AGENTS:
                raise ValueError(f"T{threat_id} 最多固定 {MIN_CAPTURE_AGENTS} 个平台（UAV+USV 合计）")

    def fixed_capture_assignments(self) -> Dict[int, int]:
        """把界面的 T 编号转成算法目标行号；已捕获小队由 guarding_agents 接管。"""
        self._validate_manual_capture_assignments(self.manual_capture_assignments)
        target_indices = {task.threat_id: i for i, task in enumerate(self.threats)}
        return {index: target_indices[threat_id]
                for index, threat_id in self.manual_capture_assignments.items()
                if self.threats[target_indices[threat_id]].state != "captured"}

    def manual_capture_summary(self) -> List[str]:
        if not self.manual_capture_assignments:
            return ["固定分配：无（全部自动）"]
        result = []
        for threat_id in sorted(set(self.manual_capture_assignments.values())):
            names = [self.platforms[i].identifier
                     for i in sorted(self.manual_capture_assignments)
                     if self.manual_capture_assignments[i] == threat_id]
            result.append(f"固定 T{threat_id}：" + " ".join(names))
        return result

    # [新增 TASK04] 指令先完整校验再切换任务，输入错误不会中断现有任务。
    def execute_command(self, text: str) -> str:
        """只解析任务命令，不执行任意 Python/shell 代码。"""
        text = text.strip()
        if text in PRESET_CAPTURE_COMMANDS:
            expanded = PRESET_CAPTURE_COMMANDS[text]
            return f"{text} → {expanded}；{self.execute_command(expanded)}"
        if text.lower() in {"0", "c", "cancel", "retreat", "withdraw", "取消", "撤退"}:
            return self.cancel_to_escort()
        if text.lower() == "presets":
            return " | ".join(f"{k}: {v}" for k, v in PRESET_CAPTURE_COMMANDS.items())
        tokens = text.replace("，", " ").replace(",", " ").split()
        if not tokens:
            raise ValueError("请输入指令，例如 chase U1 T1；help 查看帮助")
        command, args = tokens[0].lower(), [token.upper() for token in tokens[1:]]
        if command == "help" and not args:
            return ("S: 随机单目标 | targets 1/2: 生成目标 | chase U1 T1: 追击 | unchase U1: 单个撤退 | "
                    "bind T1 U1 S1: 固定成员 | unbind/clear/list | 19/G/guard: 守卫 | "
                    "20/capture/J: 围捕 | 0/C/F4/cancel/retreat: 全体撤退 | presets: 数字表")
        if command == "list" and not args:
            return "；".join(self.manual_capture_summary() + self.pursuit_summary())
        if command == "targets":
            if len(args) != 1 or args[0] not in {"1", "2"}:
                raise ValueError("格式：targets 1 或 targets 2（立即生成新一轮目标）")
            self.target_count = int(args[0])
            self.reset()
            return self.last_message
        if command == "capture" and not args:
            self.start_gbsflacs_capture()
            return self.last_message
        if command == "guard" and not args:
            # 保留用户预设，结束追击或围捕，重新按最近三人布防。
            saved = dict(self.manual_capture_assignments)
            self.cancel_to_escort()
            self.manual_capture_assignments = saved
            self.escort_only = False
            self._detect_new_threats()
            self._replan_detected_guards()
            self.phase = "自动守卫：每目标最近三人"
            self.last_message = "已恢复自动守卫；未感知目标将在感知后分配守卫，20/J 启动围捕"
            return self.last_message
        if command not in {"chase", "unchase", "bind", "unbind", "clear"}:
            raise ValueError("未知指令或参数；输入 help 查看格式")
        names = {p.identifier.upper(): i for i, p in enumerate(self.platforms)}

        def parse_target(token: str) -> int:
            if not token.startswith("T") or not token[1:].isdigit():
                raise ValueError("目标格式应为 T1 或 T2")
            threat_id = int(token[1:])
            if threat_id not in {task.threat_id for task in self.spawned_threats}:
                raise ValueError(f"未知目标：{token}；先用 targets 1/2 或 N 生成目标")
            return threat_id

        def parse_platforms(values: List[str]) -> List[int]:
            if not values:
                raise ValueError("至少指定一个平台，例如 U1 或 S1")
            if len(values) != len(set(values)):
                raise ValueError("同一条指令中不能重复指定平台")
            unknown = [name for name in values if name not in names]
            if unknown:
                raise ValueError("未知平台：" + " ".join(unknown))
            return [names[name] for name in values]

        if command == "chase":
            if len(args) != 2:
                raise ValueError("格式：chase U1 T1（一个平台追击一个目标）")
            index = parse_platforms(args[:1])[0]
            threat_id = parse_target(args[1])
            if not self.pursuit_assignments:
                self.cancel_to_escort()
            self.pursuit_assignments[index] = threat_id
            platform = self.platforms[index]
            platform.role = "pursuit"
            platform.assigned_threat_id = threat_id
            self.phase = "单平台追击"
            self.last_message = f"{platform.identifier} 开始追击 T{threat_id}；F4/0 撤退，unchase {platform.identifier} 单独撤退"
            return self.last_message
        if command == "unchase":
            if len(args) != 1:
                raise ValueError("格式：unchase U1")
            index = parse_platforms(args)[0]
            if index not in self.pursuit_assignments:
                raise ValueError(f"{args[0]} 当前未执行单平台追击")
            del self.pursuit_assignments[index]
            platform = self.platforms[index]
            platform.role = "escort"
            platform.assigned_threat_id = None
            platform.altitude = float(platform.position[2]) if platform.kind == "UAV" else 0.0
            platform.goal = self._normal_ring_goals()[index].copy()
            self.phase = "单平台追击" if self.pursuit_assignments else "手动护航：已撤退"
            self.last_message = f"{platform.identifier} 已结束追击并返回护航"
            return self.last_message

        if self.gbsflacs_active or self.capture_success or self.capture_start_frame is not None:
            raise ValueError("请先用 0/C/F4/cancel 撤退，再指定成员")
        if not self.threats:
            raise ValueError("请先按 N 或输入 targets 1/2 生成目标")
        proposed = dict(self.manual_capture_assignments)
        if command == "bind":
            if len(args) < 2:
                raise ValueError("格式：bind T1 U1 S1 U2（可只指定部分成员，每目标最多 3 个）")
            threat_id = parse_target(args[0])
            for index in parse_platforms(args[1:]):
                old_target = proposed.get(index)
                if old_target is not None and old_target != threat_id:
                    raise ValueError(f"{self.platforms[index].identifier} 已固定给 T{old_target}，请先 unbind")
                proposed[index] = threat_id
        elif command == "unbind":
            for index in parse_platforms(args):
                proposed.pop(index, None)
        else:
            if len(args) > 1:
                raise ValueError("格式：clear 或 clear T1")
            if args:
                threat_id = parse_target(args[0])
                proposed = {i: t for i, t in proposed.items() if t != threat_id}
            else:
                proposed = {}
        self._validate_manual_capture_assignments(proposed)
        self.manual_capture_assignments = proposed
        self.last_message = "固定分配已更新；20/J 后生效，按最近距离补足每目标 3 人"
        return "；".join(self.manual_capture_summary())

    def pursuit_summary(self) -> List[str]:
        if not self.pursuit_assignments:
            return []
        return ["追击：" + "，".join(f"{self.platforms[i].identifier}→T{t}"
                                    for i, t in sorted(self.pursuit_assignments.items()))]

    # [新增 TASK04] 单平台追击独立于围捕成功判定；其他平台继续护航。
    def _step_pursuit(self) -> None:
        for index, threat_id in self.pursuit_assignments.items():
            platform = self.platforms[index]
            target = self.get_threat(threat_id)
            waypoint = with_altitude(target.position, platform.altitude)
            self._move_platform_to_gbsflacs_waypoint(index, waypoint)
            platform.role = "pursuit"
            platform.assigned_threat_id = threat_id
        self._move_idle_escorts()
        self.phase = "单平台追击：" + "，".join(
            f"{self.platforms[i].identifier}→T{t}" for i, t in sorted(self.pursuit_assignments.items()))

    def _move_idle_escorts(self) -> None:
        goals = self._normal_ring_goals()
        for index, platform in enumerate(self.platforms):
            if platform.role == "escort":
                self._move_platform_safely(index, goals[index], include_repulsion=True)

    # [修改 TASK05] 任意任务立即退出，清除追击/围捕状态，原地开始撤退并保持护航。
    def cancel_to_escort(self) -> str:
        self.gbsflacs_active = False
        self.capture_success = False
        self.escort_only = True
        self.reserve_guard_indices = []
        self.support_guard_indices = []
        self._reserve_slot_offsets = {}
        self._reserve_slot_by_index = {}
        self._support_slot_by_index = {}
        self._last_detected_ids = ()
        self._own_target_bypass_side = {}
        self.avoid_direction = np.zeros(2, dtype=float)
        for task in self.threats:
            task.guard_quota = 0
            task.core_guard_index = None
            task.wing_guard_indices = []
            task.wing_slot_by_index = {}
            task.core_motion_state = "idle"
            task.core_dispatch_origin = None
            task.core_trajectory = []
            if task.state in DETECTED_STATES or task.state == "captured":
                task.state = "detected"
                task.current_speed_limit = self.enemy_forming_speed
                task.orbit_segment_remaining = 0.0
                task.orbit_angular_speed_current = 0.0
        for platform in self.platforms:
            platform.role = "escort"
            platform.assigned_threat_id = None
            platform.altitude = float(platform.position[2]) if platform.kind == "UAV" else 0.0
        self._reset_gbsflacs_runtime()
        for index, goal in self._normal_ring_goals().items():
            self.platforms[index].goal = goal.copy()
        self.phase = "手动护航：已撤退"
        self.last_message = "已撤退并清除追击/固定分配；返回护航，19/G 恢复守卫，20/J 围捕，N 新建目标"
        return self.last_message

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        return self.paused

    def _reset_gbsflacs_runtime(self) -> None:
        """Clear every target-specific GBSFLACS state for a newly spawned target."""
        self._clear_rotating_escort()  # [修改 ROTATE02] 新目标/撤退不能沿用旧的旋转基准。
        self.pursuit_assignments = {}
        self.capture_agent_ids = None
        self.permanently_captured = set()
        self.guarding_agents = {}
        self.capture_final_waypoints = {}  # [修改 M06] N 键必须清除旧目标航点。
        self.capture_start_frame = None
        self.manual_capture_assignments = {}  # [新增 CMD01] 新目标不能沿用上一轮绑定。
        self.previous_assignments = {}
        self.ball_tree = None
        self.last_gbsflacs_assignments = {}
        self.gbsflacs_algorithm = GBSFLACSAlgorithm(self)
        self._sync_gbsflacs_environment()

    def _get_guarding_agent_ids(self) -> set:
        """返回已捕获目标的小队成员，禁止再次参与其他目标的分配。"""
        result = set()
        for agent_ids in self.guarding_agents.values():
            result.update(int(agent_id) for agent_id in agent_ids)
        return result

    def _sync_gbsflacs_environment(self) -> None:
        """Expose the escort scene through the array contract used by GBSFLACS."""
        rows = []
        for index, platform in enumerate(self.platforms):
            rows.append(
                [
                    float(platform.position[0]),
                    float(platform.position[1]),
                    float(platform.position[2]),
                    0.0,
                    0.0,
                    0.0,
                    0.0 if platform.kind == "UAV" else 1.0,
                    float(index),
                ]
            )
        self.agents = np.asarray(rows, dtype=float).reshape((-1, 8))

        target_rows = []
        # [修改 M02] 不压缩 waiting 行，防止目标数组与任务列表索引错位。
        for task in self.threats:
            target_rows.append(
                [
                    float(task.position[0]),
                    float(task.position[1]),
                    0.0,
                    float(task.current_speed_limit),
                    float(task.controlled_angle),
                    float(task.threat_id),
                ]
            )
        self.targets = np.asarray(target_rows, dtype=float).reshape((-1, 6))

    def _move_platform_to_gbsflacs_waypoint(
            self,
            index: int,
            waypoint: np.ndarray,
            target_index: Optional[int] = None,
    ) -> None:
        """Move one platform toward a GBSFLACS waypoint in full three dimensions."""
        platform = self.platforms[index]
        desired = np.asarray(waypoint, dtype=float).copy()
        if desired.shape != (3,) or not np.all(np.isfinite(desired)):
            desired = platform.position.copy()
        if platform.kind == "USV":
            desired[2] = 0.0
        else:
            desired[2] = float(np.clip(desired[2], UAV_ALTITUDE_MIN, VIEW_Z_MAX))

        delta = desired - platform.position
        distance = float(np.linalg.norm(delta))
        max_step = platform.max_speed * self.dt
        if distance > EPS:
            platform.position = platform.position + delta * (min(distance, max_step) / distance)
        platform.position = self._clip_position_to_world(platform.position, margin=0.2)
        platform.position[2] = (
            0.0
            if platform.kind == "USV"
            else float(np.clip(platform.position[2], UAV_ALTITUDE_MIN, VIEW_Z_MAX))
        )
        platform.goal = desired.copy()
        platform.role = "capture"

        if (
                target_index is not None
                and 0 <= target_index < len(self.threats)
        ):
            platform.assigned_threat_id = (
                self.threats[target_index].threat_id
            )

    def _step_gbsflacs_capture(self) -> None:
        """
        同时对所有未捕获目标运行一次 GBSFLACS，
        并让各平台朝各自目标的围捕槽位移动。
        """

        self._sync_gbsflacs_environment()

        assignments = self.gbsflacs_algorithm.step()

        self.last_gbsflacs_assignments = {
            int(agent_id): int(target_id)
            for agent_id, target_id
            in assignments.items()
        }

        waypoints = (
            self.gbsflacs_algorithm.desired_waypoints
        )

        guarding_ids = self._get_guarding_agent_ids()

        for index, platform in enumerate(
                self.platforms
        ):

            # [修改 M06] 已捕获小队只执行自己的冻结航点，不再参与优化。
            if index in guarding_ids:
                continue

            target_index = (
                self.last_gbsflacs_assignments.get(index)
            )

            if target_index is None:
                continue

            waypoint = waypoints.get(
                index,
                platform.position
            )

            self._move_platform_to_gbsflacs_waypoint(
                index,
                waypoint,
                target_index=target_index,
            )

        self._move_captured_guards()
        self._move_idle_escorts()
        self.previous_assignments = (
            self.last_gbsflacs_assignments.copy()
        )

        self._sync_gbsflacs_environment()

    # [修改 M06] 部分捕获与全部捕获共用同一段完成队形逻辑。
    def _move_captured_guards(self) -> None:
        for target_index, agent_ids in self.guarding_agents.items():
            for agent_id in sorted(agent_ids):
                waypoint = self.capture_final_waypoints.get(agent_id)
                if waypoint is not None:
                    self._move_platform_to_gbsflacs_waypoint(agent_id, waypoint, target_index)
                    self.platforms[agent_id].role = "guard"

    def _maximum_capture_angular_gap(
        self, platform_indices: Sequence[int], target_position: np.ndarray
    ) -> float:
        """Return the largest uncovered XY bearing interval around the target."""
        indices = [int(index) for index in platform_indices]
        if len(indices) < 2:
            return float(2.0 * np.pi)
        target_xy = horizontal(target_position)
        vectors = np.asarray(
            [horizontal(self.platforms[index].position) - target_xy for index in indices],
            dtype=float,
        )
        vectors = vectors[np.linalg.norm(vectors, axis=1) > 1e-9]
        if len(vectors) < 2:
            return float(2.0 * np.pi)
        angles = np.sort(
            np.mod(np.arctan2(vectors[:, 1], vectors[:, 0]), 2.0 * np.pi)
        )
        gaps = np.diff(np.r_[angles, angles[0] + 2.0 * np.pi])
        return float(np.max(gaps))

    # [修改 M13] 判定与面板共用同一口径：仅统计本目标获分配的、半径内的平台。
    def _capture_members_in_range(self, target_index: int) -> List[int]:
        task = self.threats[target_index]
        if task.state == "captured":
            assigned = self.guarding_agents.get(target_index, set())
        else:
            guarding_ids = self._get_guarding_agent_ids()
            assigned = [int(a) for a, t in self.last_gbsflacs_assignments.items()
                        if int(t) == target_index and int(a) not in guarding_ids]
        return sorted(
            index for index in assigned
            if 0 <= index < len(self.platforms)
            and np.linalg.norm(self.platforms[index].position - task.position)
            <= GBSFLACS_CAPTURE_RADIUS + EPS
        )

    # [修改 TASK03] 同一目标至少三名参战成员进入三维半径即成功，无角隙/类型条件。
    def _capture_condition_met(self, target_index: int) -> bool:
        if not 0 <= target_index < len(self.threats):
            return False
        return (self.threats[target_index].state in DETECTED_STATES
                and len(self._capture_members_in_range(target_index)) >= MIN_CAPTURE_AGENTS)

    def _update_gbsflacs_capture_state(
            self
    ) -> bool:
        """
        同时检查全部目标。

        一个目标成功：
            只冻结该目标；
            锁定该目标的 3 个围捕成员；
            其他目标继续围捕。

        所有目标成功：
            才结束整个 GBSFLACS。
        """

        if not self.threats:
            return False

        newly_captured = False

        for target_index, task in enumerate(
                self.threats
        ):

            if task.state == "captured":
                continue

            if not self._capture_condition_met(
                    target_index
            ):
                continue

            # --------------------------------------------
            # 当前目标捕获成功
            # --------------------------------------------
            task.state = "captured"
            task.current_speed_limit = 0.0
            task.orbit_angular_speed_current = 0.0
            task.orbit_segment_remaining = 0.0
            task.position[2] = 0.0

            self.permanently_captured.add(
                target_index
            )

            # 当前负责该目标的 3 个平台锁定为守卫
            guard_ids = {
                int(agent_id)
                for agent_id, assigned_target
                in self.last_gbsflacs_assignments.items()
                if int(assigned_target)
                   == target_index
            }

            self.guarding_agents[
                target_index
            ] = guard_ids

            for agent_id in guard_ids:
                platform = self.platforms[agent_id]

                platform.role = "guard"

                platform.assigned_threat_id = (
                    task.threat_id
                )

                # [修改 M06] 保存成功帧最后航点，较早捕获的小队也可完成交替队形。
                final = self.gbsflacs_algorithm.desired_waypoints.get(
                    agent_id, platform.position
                )
                self.capture_final_waypoints[agent_id] = np.asarray(final, dtype=float).copy()
                platform.goal = self.capture_final_waypoints[agent_id].copy()

            newly_captured = True
            start_frame = self.capture_start_frame if self.capture_start_frame is not None else self.frame
            self.gbsflacs_algorithm.metrics['capture_time'].append((self.frame - start_frame) * self.dt)

            print(
                f"GBSFLACS 围捕成功："
                f"目标 T{task.threat_id} 至少 3 个成员已进入捕获半径；"
                f"守卫平台={sorted(guard_ids)}"
            )

        # --------------------------------------------------
        # 判断是否全部成功
        # --------------------------------------------------
        total_targets = len(self.threats)

        captured_count = sum(
            task.state == "captured"
            for task in self.threats
        )

        if (
                total_targets > 0
                and captured_count == total_targets
        ):
            self.capture_success = True
            self.gbsflacs_active = False

            self.phase = (
                "GBSFLACS 多目标三维围捕全部成功"
            )

            self.last_message = (
                f"GBSFLACS 围捕完成："
                f"{captured_count}/{total_targets} "
                "个目标全部捕获"
            )

        else:
            self.capture_success = False
            self.gbsflacs_active = True

            self.phase = (
                f"GBSFLACS 多目标围捕中："
                f"已完成 {captured_count}/"
                f"{total_targets}"
            )

        # [修改 M14] 捕获发生在移动之后，立即更新成功率。
        self.gbsflacs_algorithm.metrics['success_rate'] = calculate_success_rate(
            self.permanently_captured, total_targets
        )
        self._sync_gbsflacs_environment()

        return newly_captured

    # [修改 TASK02] 派遣时只选每目标三人；剩余平台留在护航环。
    def start_gbsflacs_capture(self) -> bool:
        if not self.threats:
            self.last_message = "当前没有目标，请先按 N 或输入 targets 1/2"
            return False
        if self.gbsflacs_active:
            self.last_message = "GBSFLACS 围捕正在进行"
            return False
        active_targets = {i: task.position for i, task in enumerate(self.threats)
                          if task.state != "captured"}
        if not active_targets:
            self.last_message = "所有目标均已完成围捕"
            return False
        missing = [self.threats[i].threat_id for i in active_targets
                   if self.threats[i].state not in DETECTED_STATES]
        if missing:
            self.last_message = "以下目标尚未进入感知范围：" + ", ".join(f"T{t}" for t in missing)
            return False
        guards = self._get_guarding_agent_ids()
        try:
            plan = nearest_team_assignment(
                {i: p.position for i, p in enumerate(self.platforms) if i not in guards},
                active_targets, fixed=self.fixed_capture_assignments(),
            )
        except ValueError as exc:
            self.last_message = f"无法启动围捕：{exc}"
            return False
        # 通过全部校验后才接管追击/守卫任务；失败时保持原任务。
        self._clear_rotating_escort()  # 围捕阶段按原逻辑让未参战者护航。
        self.pursuit_assignments = {}
        self.capture_agent_ids = set(plan) | guards
        self.escort_only = False
        self.gbsflacs_active = True
        self.capture_success = False
        self.capture_start_frame = self.frame
        self.reserve_guard_indices = []
        self.support_guard_indices = []
        self._reserve_slot_by_index = {}
        self._support_slot_by_index = {}
        for task in self.threats:
            task.guard_quota = 0
            task.wing_guard_indices = []
            task.wing_slot_by_index = {}
        for index, platform in enumerate(self.platforms):
            if index in guards:
                continue
            platform.role = "capture" if index in plan else "escort"
            platform.assigned_threat_id = self.threats[plan[index]].threat_id if index in plan else None
            platform.altitude = float(platform.position[2]) if platform.kind == "UAV" else 0.0
        self._sync_gbsflacs_environment()
        self.last_gbsflacs_assignments = dict(plan)
        self.previous_assignments = dict(plan)
        self.gbsflacs_algorithm.last_assignments = dict(plan)
        self.phase = f"GBSFLACS 三维围捕中：{len(active_targets)} 个目标，各 3 人"
        self.last_message = "围捕已启动：固定成员优先，其余按最近距离补齐；F4/0 随时撤退"
        return True

    def _spawn_due_threats(self) -> None:
        for task in self.threats:
            if task.state == "waiting" and self.frame >= task.spawn_frame:
                task.position = point3(
                    horizontal(self.own_position)
                    + task.spawn_radius
                    * np.array([math.cos(task.spawn_angle), math.sin(task.spawn_angle)], dtype=float),
                    0.0,
                )
                task.state = "approaching"
                task.current_speed_limit = self.enemy_approach_speed
                self.last_message = f"敌方目标 T{task.threat_id} 已从随机方向出现"

    def _move_point_toward(
        self, current: np.ndarray, desired: np.ndarray, speed_limit: float
    ) -> np.ndarray:
        current_arr = np.asarray(current, dtype=float)
        desired_arr = np.asarray(desired, dtype=float)
        delta = horizontal(desired_arr) - horizontal(current_arr)
        distance = float(np.linalg.norm(delta))
        max_step = max(0.0, float(speed_limit)) * self.dt
        # [修改 M08] 二维输入也构造三维结果，避免 result[2] 越界。
        result = point3(current_arr, current_arr[2] if current_arr.shape[0] >= 3 else 0.0)
        if distance <= max_step + EPS:
            result[:2] = horizontal(desired_arr)
        else:
            result[:2] = horizontal(current_arr) + delta * (max_step / (distance + EPS))
        result[2] = current_arr[2] if current_arr.shape[0] >= 3 else 0.0
        return result

    def _move_enemy(self, task: ThreatTask) -> None:
        if task.state == "waiting":
            task.current_speed_limit = 0.0
            task.position[2] = 0.0
            return
        if task.state == "captured":
            task.current_speed_limit = 0.0
            task.position[2] = 0.0
            return
        relative = horizontal(task.position) - horizontal(self.own_position)
        direction = normalize(relative, np.array([1.0, 0.0]))
        if task.state == "approaching":
            task.current_speed_limit = self.enemy_approach_speed
            desired = with_altitude(self.own_position, 0.0)
            task.position = self._move_point_toward(task.position, desired, task.current_speed_limit)
            task.position = self._clip_position_to_world(task.position, margin=0.2)
            task.position[2] = 0.0
            return
        if task.state in {"detected", "forming"}:
            task.current_speed_limit = self.enemy_forming_speed
            target_radius = self._controlled_track_radius(task)
            desired = point3(horizontal(self.own_position) + direction * target_radius, 0.0)
            task.position = self._move_point_toward(task.position, desired, task.current_speed_limit)
            task.position = self._clip_position_to_world(task.position, margin=0.2)
            task.position[2] = 0.0
            return
        task.current_speed_limit = self.enemy_controlled_speed
        if task.state == "orbiting":
            if task.orbit_segment_remaining <= EPS:
                self._start_random_orbit_segment(task)
            angular_step = min(
                task.orbit_segment_remaining,
                task.orbit_angular_speed_current * self.dt,
            )
            task.controlled_angle += task.orbit_direction * angular_step
            task.orbit_segment_remaining = max(0.0, task.orbit_segment_remaining - angular_step)
            desired = point3(
                horizontal(self.own_position)
                + task.controlled_radius
                * np.array([math.cos(task.controlled_angle), math.sin(task.controlled_angle)], dtype=float),
                0.0,
            )
            task.position = self._move_point_toward(task.position, desired, task.current_speed_limit)
            task.position = self._clip_position_to_world(task.position, margin=0.2)
            task.position[2] = 0.0
            if task.orbit_segment_remaining <= EPS:
                self._start_random_orbit_segment(task)
            return

    def _detect_new_threats(self) -> bool:
        changed = False
        for task in self.threats:
            if task.state != "approaching":
                continue
            distance = horizontal_distance(task.position, self.own_position)
            if distance <= self.sensor_radius + EPS:
                task.state = "detected"
                task.detected_frame = self.frame
                changed = True
                self.last_message = f"感知到敌方目标 T{task.threat_id}，触发守卫机制"
        return changed

    def _threat_geometry(self, task: ThreatTask) -> Tuple[np.ndarray, np.ndarray, float]:
        delta = horizontal(task.position) - horizontal(self.own_position)
        distance = float(np.linalg.norm(delta))
        return delta, normalize(delta, self.forward), distance

    def _refresh_task_blocker(self, task: ThreatTask) -> None:
        task.blocker_point, task.blocker_t = compute_blocker_point(
            self.own_position,
            task.position,
            ratio=self.blocker_ratio,
            r_min=self.blocker_r_min,
            r_max=self.blocker_r_max,
            fallback_direction=self.forward,
        )

    def _refresh_all_blockers(self) -> None:
        for task in self.detected_threats:
            self._refresh_task_blocker(task)

    @staticmethod
    def _minimum_cost_assignment(cost_matrix: np.ndarray) -> List[int]:
        """使用 O(n^3) 匈牙利算法完成矩形最小代价唯一分配。"""
        costs = np.asarray(cost_matrix, dtype=float)
        if costs.ndim != 2:
            raise ValueError("cost_matrix must be two-dimensional")
        rows, columns = costs.shape
        if rows == 0:
            return []
        if rows > columns:
            raise ValueError("there must be at least as many candidates as slots")
        if not np.all(np.isfinite(costs)):
            raise ValueError("cost_matrix must contain only finite values")

        # [修改 M09] 复用已有 SciPy 依赖，移除重复的手写匈牙利算法。
        row_indices, column_indices = linear_sum_assignment(costs)
        assignment = np.empty(rows, dtype=int)
        assignment[row_indices] = column_indices
        return assignment.tolist()

    def guard_quota_per_detected_target(self) -> Dict[int, int]:
        return {task.threat_id: GUARD_AGENTS_PER_TARGET for task in self.detected_threats}

    #新增
    def _guard_layer_index(
            self,
            task: ThreatTask
    ) -> int:
        """
        根据目标编号确定稳定的守卫层。

        T1 -> 第0层
        T2 -> 第1层
        T3 -> 第2层
        ...
        """

        return max(
            0,
            int(task.threat_id) - 1
        )

    def _guard_layer_radius(
            self,
            task: ThreatTask
    ) -> float:
        """
        为不同目标建立不同半径的守卫层。
        """

        layer = self._guard_layer_index(task)

        return float(
            self.guard_arc_radius
            + layer * GUARD_LAYER_GAP
        )

    def _guard_arc_geometry(
            self,
            task: ThreatTask
    ) -> Tuple[float, float]:
        """
        返回当前目标对应的分层守卫弧参数。

        不再预留核心守卫位置。
        """

        guard_count = max(
            0,
            int(task.guard_quota)
        )

        radius = self._guard_layer_radius(task)

        if guard_count <= 1:
            return radius, 0.0

        half_angle = min(
            self.max_guard_arc_half_angle,
            max(
                self.guard_arc_half_angle,
                math.radians(8.0)
                * (guard_count - 1)
            ),
        )

        angular_step = (
                2.0 * half_angle
                / max(guard_count - 1, 1)
        )

        # 确保同一层相邻守卫之间也保持最小距离
        if self.minimum_guard_spacing > EPS:
            radius = max(
                radius,
                self.minimum_guard_spacing
                / (
                        2.0
                        * max(
                    math.sin(
                        angular_step / 2.0
                    ),
                    1e-6,
                )
                ),
            )

        return float(radius), float(half_angle)

    def wing_goals(
            self,
            task_or_id: Union[ThreatTask, int]
    ) -> List[np.ndarray]:

        task = (
            task_or_id
            if isinstance(task_or_id, ThreatTask)
            else self.get_threat(task_or_id)
        )

        guard_count = max(
            0,
            int(task.guard_quota)
        )

        if guard_count == 0:
            return []

        _, threat_dir, _ = (
            self._threat_geometry(task)
        )

        lateral = rotate90(threat_dir)

        radius, half_angle = (
            self._guard_arc_geometry(task)
        )

        if guard_count == 1:

            angles = [0.0]

        else:

            angles = np.linspace(
                -half_angle,
                half_angle,
                guard_count
            )

        goals = []

        for phi in angles:
            goal_xy = (
                    horizontal(self.own_position)
                    + radius
                    * (
                            math.cos(phi) * threat_dir
                            + math.sin(phi) * lateral
                    )
            )

            goals.append(
                point3(goal_xy, 0.0)
            )

        return goals

    def support_goals(
        self, task_or_id: Union[ThreatTask, int], count: Optional[int] = None
    ) -> List[np.ndarray]:
        """Return a rear support arc on the sea plane that rotates with the threat."""
        task = task_or_id if isinstance(task_or_id, ThreatTask) else self.get_threat(task_or_id)
        support_count = len(self.support_guard_indices) if count is None else int(count)
        if support_count <= 0:
            return []
        _, threat_dir, _ = self._threat_geometry(task)
        center_dir = np.array(
            [
                math.cos(self.support_arc_rear_offset) * threat_dir[0]
                - math.sin(self.support_arc_rear_offset) * threat_dir[1],
                math.sin(self.support_arc_rear_offset) * threat_dir[0]
                + math.cos(self.support_arc_rear_offset) * threat_dir[1],
            ],
            dtype=float,
        )
        lateral = rotate90(center_dir)
        angles: Iterable[float]
        if support_count == 1:
            angles = [0.0]
        else:
            angles = np.linspace(-self.support_arc_half_angle, self.support_arc_half_angle, support_count)
        return [
            point3(
                horizontal(self.own_position)
                + self.support_guard_radius
                * (math.cos(phi) * center_dir + math.sin(phi) * lateral),
                0.0,
            )
            for phi in angles
        ]

    def _current_reserve_goal_offsets(self) -> List[np.ndarray]:
        if not self.reserve_guard_indices:
            return []
        threat_dir = self.composite_threat_direction()
        rear = -threat_dir
        lateral = rotate90(rear)
        if len(self.reserve_guard_indices) == 1:
            return [self.ring_radius * rear]
        return [
            self.ring_radius * normalize(rear + 0.55 * lateral),
            self.ring_radius * normalize(rear - 0.55 * lateral),
        ]

    def _current_reserve_goals(self) -> Dict[int, np.ndarray]:
        offsets = self._current_reserve_goal_offsets()
        result: Dict[int, np.ndarray] = {}
        for index, slot in self._reserve_slot_by_index.items():
            if slot < len(offsets):
                platform = self.platforms[index]
                result[index] = point3(horizontal(self.own_position) + offsets[slot], platform.altitude)
        return result

    def _assign_reserve_slots(self) -> None:
        self._reserve_slot_offsets = {}
        self._reserve_slot_by_index = {}
        if not self.reserve_guard_indices:
            return
        offsets = self._current_reserve_goal_offsets()
        candidates = self.reserve_guard_indices
        cost = np.zeros((len(offsets), len(candidates)), dtype=float)
        for row, offset in enumerate(offsets):
            goal = point3(horizontal(self.own_position) + offset, 0.0)
            for col, index in enumerate(candidates):
                cost[row, col] = horizontal_distance(self.platforms[index].position, goal)
        assignment = self._minimum_cost_assignment(cost)
        for slot, column in enumerate(assignment):
            index = candidates[column]
            self._reserve_slot_by_index[index] = slot
            self._reserve_slot_offsets[index] = offsets[slot]



    # [修改 TASK02] 先按目标距离选出三人小队，再为小队匹配原有分层守卫弧。
    def _replan_detected_guards(self) -> None:
        tasks = sorted(self.detected_threats, key=lambda task: task.threat_id)
        self._own_target_bypass_side = {}
        self.reserve_guard_indices = []
        self.support_guard_indices = []
        self._support_slot_by_index = {}
        for platform in self.platforms:
            platform.role = "escort"
            platform.assigned_threat_id = None
        for task in self.threats:
            task.guard_quota = 0
            task.core_guard_index = None
            task.wing_guard_indices = []
            task.wing_slot_by_index = {}
        plan = nearest_team_assignment(
            {i: p.position for i, p in enumerate(self.platforms)},
            {task.threat_id: task.position for task in tasks},
        )
        for task in tasks:
            members = sorted(i for i, t in plan.items() if t == task.threat_id)
            task.guard_quota = GUARD_AGENTS_PER_TARGET
            goals = self.wing_goals(task)
            cost = np.array([[horizontal_distance(self.platforms[i].position, goal)
                              for goal in goals] for i in members])
            rows, cols = linear_sum_assignment(cost)
            for row, col in zip(rows, cols):
                index = members[int(row)]
                task.wing_guard_indices.append(index)
                task.wing_slot_by_index[index] = int(col)
                self.platforms[index].role = "guard"
                self.platforms[index].assigned_threat_id = task.threat_id
            if task.state == "detected":
                task.state = "forming"
        # 预留只用未参战者；两个目标时全部六人布防，不能抢占目标小队。
        idle = [i for i in range(len(self.platforms)) if i not in plan]
        if tasks:
            self.reserve_guard_indices = idle[:self.escort_reserve_count]
        for i in self.reserve_guard_indices:
            self.platforms[i].role = "reserve"
        self._assign_reserve_slots()
        if len(tasks) == 1:
            self._anchor_rotating_escort(tasks[0], idle)
        else:
            self._clear_rotating_escort()
        self._last_detected_ids = tuple(task.threat_id for task in tasks)

    def _synchronize_guard_plan(self) -> None:
        ids = tuple(sorted(task.threat_id for task in self.detected_threats))
        if ids != self._last_detected_ids:
            self._replan_detected_guards()

    def composite_threat_direction(self) -> np.ndarray:
        tasks = self.detected_threats
        if not tasks:
            return normalize(self.forward, np.array([1.0, 0.0]))
        combined = np.zeros(2, dtype=float)
        nearest_direction = normalize(self.forward)
        nearest_distance = math.inf
        for task in tasks:
            _, direction, distance = self._threat_geometry(task)
            combined += direction / max(distance, 1.0) ** 2
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_direction = direction
        return normalize(combined, nearest_direction)

    def _resolve_avoid_direction(self) -> np.ndarray:
        left = rotate90(normalize(self.forward, np.array([1.0, 0.0])))
        if self.avoidance_mode == "left":
            return left
        if self.avoidance_mode == "right":
            return -left
        lateral = float(np.dot(self.composite_threat_direction(), left))
        if lateral > 0.05:
            return -left
        if lateral < -0.05:
            return left
        return -left

    def _truncate_own_step_for_platforms(
        self, start: np.ndarray, end: np.ndarray
    ) -> np.ndarray:
        """连续缩短我方目标水平航步，避免移动中心主动穿入任一平台。"""
        start_xy = horizontal(start)
        end_xy = horizontal(end)
        direction = end_xy - start_xy
        a = float(np.dot(direction, direction))
        if a <= EPS:
            return point3(start_xy, 0.0)
        allowed = 1.0
        radius = self.own_target_avoid_radius
        for platform in self.platforms:
            relative = start_xy - horizontal(platform.position)
            start_distance = float(np.linalg.norm(relative))
            if start_distance < radius - 1e-9:
                allowed = 0.0
                continue
            closest_parameter = float(np.clip(-np.dot(relative, direction) / a, 0.0, 1.0))
            closest = relative + closest_parameter * direction
            if float(np.linalg.norm(closest)) >= radius - 1e-10:
                continue
            b = 2.0 * float(np.dot(relative, direction))
            c = float(np.dot(relative, relative) - radius**2)
            discriminant = max(0.0, b * b - 4.0 * a * c)
            root = math.sqrt(discriminant)
            roots = sorted(
                value
                for value in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a))
                if 0.0 <= value <= 1.0
            )
            if roots:
                allowed = min(allowed, max(0.0, roots[0] - 1e-8))
        return point3(start_xy + allowed * direction, 0.0)

    def _clip_position_to_world(self, position: np.ndarray, margin: float = 0.0) -> np.ndarray:
        result = np.asarray(position, dtype=float).copy()
        result[0] = float(np.clip(result[0], self.world_x_min + margin, self.world_x_max - margin))
        result[1] = float(np.clip(result[1], self.world_y_min + margin, self.world_y_max - margin))
        if result.shape[0] < 3:
            result = point3(result, 0.0)
        return result

    def _move_own_target(self) -> None:
        if self.escort_only or not self.detected_threats:
            velocity = normalize(self.forward) * self.cruise_speed
            self.avoid_direction = np.zeros(2, dtype=float)
        else:
            self.avoid_direction = self._resolve_avoid_direction()
            desired_velocity = normalize(self.forward) * self.forward_shift + self.avoid_direction * self.avoid_distance
            velocity = self.own_gain * desired_velocity
        speed = float(np.linalg.norm(velocity))
        if speed > self.own_max_speed:
            velocity *= self.own_max_speed / (speed + EPS)
        proposed = point3(horizontal(self.own_position) + velocity * self.dt, 0.0)
        self.own_position = self._truncate_own_step_for_platforms(self.own_position, proposed)
        self.own_position = self._clip_position_to_world(self.own_position, margin=0.8)
        self.own_position[2] = 0.0
        self.own_goal = point3(horizontal(self.own_position) + velocity, 0.0)

    # [修改 ROTATE02] 锁定未参战平台的相对位置，随后与单目标守卫方向同角度旋转。
    def _clear_rotating_escort(self) -> None:
        self._escort_rotation_target: Optional[int] = None
        self._escort_rotation_reference = np.array([1.0, 0.0], dtype=float)
        self._escort_rotation_offsets: Dict[int, np.ndarray] = {}
        self._escort_rotation_previous_goals: Dict[int, np.ndarray] = {}

    def _anchor_rotating_escort(self, task: ThreatTask, indices: Sequence[int]) -> None:
        self._clear_rotating_escort()
        self._escort_rotation_target = task.threat_id
        self._escort_rotation_reference = self._threat_geometry(task)[1].copy()
        center = horizontal(self.own_position)
        for index in indices:
            platform = self.platforms[index]
            self._escort_rotation_offsets[index] = horizontal(platform.position).copy() - center
            self._escort_rotation_previous_goals[index] = platform.position.copy()

    def _rotating_escort_goals(self) -> Dict[int, np.ndarray]:
        tasks = self.detected_threats
        if (self.escort_only or self.gbsflacs_active or self.capture_success
                or self.pursuit_assignments or len(tasks) != 1
                or tasks[0].threat_id != self._escort_rotation_target):
            return {}
        direction = self._threat_geometry(tasks[0])[1]
        reference = self._escort_rotation_reference
        cosine = float(np.dot(reference, direction))
        sine = float(reference[0] * direction[1] - reference[1] * direction[0])
        rotation = np.array([[cosine, -sine], [sine, cosine]], dtype=float)
        center = horizontal(self.own_position)
        return {index: point3(center + rotation @ offset, self.platforms[index].altitude)
                for index, offset in self._escort_rotation_offsets.items()
                if self.platforms[index].role in {"escort", "reserve"}}

    def _normal_ring_goals(self) -> Dict[int, np.ndarray]:
        count = len(self.platforms)
        result: Dict[int, np.ndarray] = {}
        for index, phi in enumerate(np.linspace(0.0, 2.0 * math.pi, count, endpoint=False)):
            platform = self.platforms[index]
            result[index] = point3(
                horizontal(self.own_position)
                + self.ring_radius * np.array([math.cos(phi), math.sin(phi)], dtype=float),
                platform.altitude,
            )
        return result

    def _desired_non_core_goals(
            self
    ) -> Dict[int, np.ndarray]:

        if self.escort_only or not self.detected_threats:
            return self._normal_ring_goals()

        # 没被分配守卫任务的平台继续普通护航
        result = self._normal_ring_goals()

        for task in self.detected_threats:

            goals = self.wing_goals(task)

            for index, slot in (
                    task.wing_slot_by_index.items()
            ):

                if slot < len(goals):
                    result[index] = with_altitude(
                        goals[slot],
                        self.platforms[index].altitude
                    )

        result.update(self._current_reserve_goals())
        # [修改 ROTATE02] 单目标下未参战三人保持原相对位置，同步平移和旋转。
        result.update(self._rotating_escort_goals())

        return result



    def _repulsion_velocity(self, index: int) -> np.ndarray:
        # [修改 M05] 使用实例参数；默认值保留原本实际生效的守卫避碰配置。
        current = horizontal(self.platforms[index].position)
        repulsion = np.zeros(2, dtype=float)
        for other_index, other in enumerate(self.platforms):
            if other_index == index:
                continue
            diff = current - horizontal(other.position)
            distance = float(np.linalg.norm(diff))
            if EPS < distance < self.safe_distance:
                repulsion += (self.repulsion_gain
                              * (1.0 / distance - 1.0 / self.safe_distance)
                              * diff / distance)
        return repulsion

    @property
    def own_target_route_radius(self) -> float:
        return self.own_target_avoid_radius + self._own_target_route_margin

    def _segment_intersects_own_target_circle(
        self, start: np.ndarray, end: np.ndarray, radius: Optional[float] = None
    ) -> bool:
        start_xy = horizontal(start)
        end_xy = horizontal(end)
        center = horizontal(self.own_position)
        protected_radius = self.own_target_avoid_radius if radius is None else float(radius)
        segment = end_xy - start_xy
        denominator = float(np.dot(segment, segment))
        if denominator <= EPS:
            return float(np.linalg.norm(start_xy - center)) < protected_radius - EPS
        parameter = float(np.clip(np.dot(center - start_xy, segment) / denominator, 0.0, 1.0))
        closest = start_xy + parameter * segment
        return float(np.linalg.norm(closest - center)) < protected_radius - EPS

    @staticmethod
    def _directed_arc_delta(start_angle: float, end_angle: float, side: int) -> float:
        if side > 0:
            return float((end_angle - start_angle) % (2.0 * math.pi))
        return float((start_angle - end_angle) % (2.0 * math.pi))

    def _bypass_path_length(self, current: np.ndarray, goal: np.ndarray, side: int) -> float:
        center = horizontal(self.own_position)
        radius = self.own_target_route_radius
        p = horizontal(current) - center
        g = horizontal(goal) - center
        pd = max(float(np.linalg.norm(p)), radius)
        gd = max(float(np.linalg.norm(g)), radius)
        pa = math.atan2(p[1], p[0])
        ga = math.atan2(g[1], g[0])
        p_alpha = math.acos(float(np.clip(radius / pd, 0.0, 1.0)))
        g_alpha = math.acos(float(np.clip(radius / gd, 0.0, 1.0)))
        start_tangent = pa + side * p_alpha
        end_tangent = ga - side * g_alpha
        arc = self._directed_arc_delta(start_tangent, end_tangent, side)
        return (
            math.sqrt(max(0.0, pd * pd - radius * radius))
            + radius * arc
            + math.sqrt(max(0.0, gd * gd - radius * radius))
        )

    def _choose_bypass_side(self, index: int, current: np.ndarray, goal: np.ndarray) -> int:
        existing = self._own_target_bypass_side.get(index)
        if existing in {-1, 1}:
            return existing
        ccw = self._bypass_path_length(current, goal, 1)
        cw = self._bypass_path_length(current, goal, -1)
        side = (1 if index % 2 == 0 else -1) if abs(ccw - cw) <= 1e-9 else (1 if ccw < cw else -1)
        self._own_target_bypass_side[index] = side
        return side

    def _truncate_step_before_safety_circle(self, start: np.ndarray, end: np.ndarray) -> np.ndarray:
        if not self._segment_intersects_own_target_circle(start, end):
            return np.asarray(end, dtype=float).copy()
        altitude = float(np.asarray(start, dtype=float)[2])
        start_xy = horizontal(start)
        end_xy = horizontal(end)
        direction = end_xy - start_xy
        a = float(np.dot(direction, direction))
        if a <= EPS:
            return point3(start_xy, altitude)
        relative = start_xy - horizontal(self.own_position)
        b = 2.0 * float(np.dot(relative, direction))
        c = float(np.dot(relative, relative) - self.own_target_avoid_radius**2)
        disc = max(0.0, b * b - 4.0 * a * c)
        root = math.sqrt(disc)
        roots = [(-b - root) / (2.0 * a), (-b + root) / (2.0 * a)]
        valid = [value for value in roots if 0.0 <= value <= 1.0]
        if not valid:
            return point3(start_xy, altitude)
        return point3(start_xy + max(0.0, min(valid) - 1e-8) * direction, altitude)

    def _safe_route_waypoint(
        self, index: int, current: np.ndarray, goal: np.ndarray, max_step: float
    ) -> np.ndarray:
        """在水平面绕过安全圆，同时保留平台固定高度。"""
        altitude = self.platforms[index].altitude
        center = horizontal(self.own_position)
        route_radius = self.own_target_route_radius
        current_xy = horizontal(current)
        goal_xy = horizontal(goal)
        current_relative = current_xy - center
        goal_relative = goal_xy - center
        current_distance = float(np.linalg.norm(current_relative))
        goal_distance = float(np.linalg.norm(goal_relative))

        if goal_distance < route_radius:
            goal_relative = normalize(goal_relative, current_relative) * route_radius
            goal_xy = center + goal_relative

        goal3 = point3(goal_xy, altitude)
        # [修改 RETREAT01] 移动中心可能让平台落入安全圆外的绕行缓冲带。
        # 此时允许沿不再靠近中心的安全直线驶离，避免必须先跨出 route_radius 才能归位。
        clearance_radius = min(route_radius, max(self.own_target_avoid_radius, current_distance))
        if (current_distance >= self.own_target_avoid_radius
                and not self._segment_intersects_own_target_circle(current, goal3, clearance_radius)):
            self._own_target_bypass_side.pop(index, None)
            return goal3

        side = self._choose_bypass_side(index, current, goal3)
        radial = normalize(current_relative, -goal_relative)
        tangent = side * rotate90(radial)
        radial_error = current_distance - route_radius
        if radial_error > max(0.25, 1.5 * max_step):
            steering = tangent - 0.85 * radial
        else:
            correction = float(np.clip(-2.5 * radial_error / max(max_step, 1e-6), -1.4, 1.8))
            steering = tangent + correction * radial
        return point3(current_xy + normalize(steering, tangent) * max_step, altitude)

    def _move_platform_safely(
        self, index: int, goal: np.ndarray, *, include_repulsion: bool = False,
        feedforward_velocity: Optional[np.ndarray] = None,
    ) -> None:
        platform = self.platforms[index]
        current = platform.position.copy()
        goal3 = with_altitude(goal, platform.altitude)
        max_step = platform.max_speed * self.dt
        # [修改 TASK05] 追击/围捕可能进入我方安全圆；撤退须允许从圆内向外脱离。
        relative = horizontal(current) - horizontal(self.own_position)
        if np.linalg.norm(relative) < self.own_target_avoid_radius:
            radial = normalize(relative, horizontal(goal3) - horizontal(self.own_position))
            platform.position = self._clip_position_to_world(
                point3(horizontal(current) + radial * max_step, platform.altitude), margin=0.2)
            platform.goal = goal3.copy()
            return
        waypoint = self._safe_route_waypoint(index, current, goal3, max_step)
        velocity = platform.gain * (horizontal(waypoint) - horizontal(current))
        # [修改 ROTATE03] 仅旋转护航成员补偿槽位平移/转动速度，减少队形方向滞后。
        # 其余任务继续使用原控制律；所有平台仍服从原速度上限、绕障和避碰。
        if (feedforward_velocity is not None
                and index not in self._own_target_bypass_side
                and np.allclose(horizontal(waypoint), horizontal(goal3), atol=1e-9)):
            feedforward = np.asarray(feedforward_velocity, dtype=float)
            previous_goal = horizontal(goal3) - feedforward * self.dt
            velocity = feedforward + platform.gain * (previous_goal - horizontal(current))
        if include_repulsion and index not in self._own_target_bypass_side:
            velocity += self._repulsion_velocity(index)
        speed = float(np.linalg.norm(velocity))
        if speed > platform.max_speed:
            velocity *= platform.max_speed / (speed + EPS)
        proposed = point3(horizontal(current) + velocity * self.dt, platform.altitude)
        proposed = self._truncate_step_before_safety_circle(current, proposed)
        platform.position = self._clip_position_to_world(proposed, margin=0.2)
        platform.position[2] = platform.altitude if platform.kind == "UAV" else 0.0
        platform.goal = goal3.copy()

    def _move_platforms(self) -> None:
        """守卫沿原弧运动；单目标时其余三人保持相对布局并同步旋转。"""
        goals = self._desired_non_core_goals()
        rotating_goals = self._rotating_escort_goals()
        for index, platform in enumerate(self.platforms):
            goal = goals.get(index, platform.position)
            feedforward = None
            if index in rotating_goals:
                previous = self._escort_rotation_previous_goals.get(index, platform.position)
                feedforward = (horizontal(goal) - horizontal(previous)) / self.dt
            self._move_platform_safely(
                index, goal, include_repulsion=True, feedforward_velocity=feedforward,
            )
        self._escort_rotation_previous_goals = {
            index: goal.copy() for index, goal in rotating_goals.items()
        }

    def wing_arrival_ratio(self, threat_id: int) -> float:
        task = self.get_threat(threat_id)
        goals = self.wing_goals(task)
        # [修改 M11] 没有守卫不代表队形已完成。
        if not task.wing_guard_indices or task.guard_quota <= 0:
            return 0.0
        arrived = 0
        for index in task.wing_guard_indices:
            slot = task.wing_slot_by_index[index]
            if slot < len(goals):
                error = horizontal_distance(self.platforms[index].position, goals[slot])
                if error <= self.wing_arrival_tolerance:
                    arrived += 1
        return arrived / max(task.guard_quota, len(task.wing_guard_indices))

    def formation_ready(
            self,
            threat_id: int
    ) -> bool:

        task = self.get_threat(
            threat_id
        )

        return bool(
            task.state
            in {
                "detected",
                "forming",
                "orbiting",
            }
            and
            self.wing_arrival_ratio(
                threat_id
            )
            + EPS
            >= self.wing_ready_ratio
        )

    def _controlled_track_radius(self, task: ThreatTask) -> float:
        """Return the single controlled orbit radius."""
        return float(
            max(
                self.enemy_min_radius,
                min(self.enemy_min_radius + 1.4, self.sensor_radius - 0.6),
            )
        )

    def controlled_track_error(self, task_or_id: Union[ThreatTask, int]) -> float:
        task = task_or_id if isinstance(task_or_id, ThreatTask) else self.get_threat(task_or_id)
        distance = horizontal_distance(task.position, self.own_position)
        return abs(distance - self._controlled_track_radius(task))

    def target_on_controlled_track(
        self, task_or_id: Union[ThreatTask, int], tolerance: Optional[float] = None
    ) -> bool:
        """Return whether a target has reached its assigned radial track."""
        tol = (
            max(
                1e-8,
                self.own_max_speed * self.dt
                + self.enemy_forming_speed * self.dt * 0.05,
            )
            if tolerance is None
            else float(tolerance)
        )
        return self.controlled_track_error(task_or_id) <= tol + EPS

    def _enter_controlled_motion(self, task: ThreatTask) -> None:
        relative = horizontal(task.position) - horizontal(self.own_position)
        task.controlled_radius = self._controlled_track_radius(task)
        task.controlled_angle = math.atan2(relative[1], relative[0])
        task.state = "orbiting"
        task.orbit_segment_count = 0
        task.orbit_direction_changes = 0
        self._start_random_orbit_segment(task)
        direction_label = "逆时针" if task.orbit_direction > 0 else "顺时针"
        self.last_message = (
            f"T{task.threat_id} 守卫队形形成，"
            f"开始随机分段环绕；"
            f"当前方向：{direction_label}"
        )

    def _update_control_transitions(self) -> None:
        for task in self.detected_threats:
            if (
                task.state in {"detected", "forming"}
                and self.formation_ready(task.threat_id)
                and self.target_on_controlled_track(task)
            ):
                self._enter_controlled_motion(task)

    def threat_bearing_label(self, task: ThreatTask) -> str:
        _, direction, _ = self._threat_geometry(task)
        left = rotate90(normalize(self.forward))
        angle = math.degrees(math.atan2(float(np.dot(direction, left)), float(np.dot(direction, self.forward))))
        if angle < 0.0:
            angle += 360.0
        labels = ("正前方", "左前方", "正左方", "左后方", "正后方", "右后方", "正右方", "右前方")
        sector = int(((angle + 22.5) % 360.0) // 45.0)
        return f"{labels[sector]}（{angle:.1f}°）"

    def step(self) -> None:
        if self.paused:
            return
        self.frame += 1
        if self.capture_success:
            for task in self.threats:
                task.current_speed_limit = 0.0
                task.position[2] = 0.0
            # [修改 M06] 所有已捕获小队分别完成各自保存的最终航点。
            self._move_captured_guards()
            self._move_own_target()
            self._move_idle_escorts()
            self._sync_gbsflacs_environment()
            self.phase = "GBSFLACS 多目标三维围捕全部成功"
            return
        self._spawn_due_threats()

        # 先让敌方目标按当前状态运动，再执行感知判定。
        for task in self.threats:
            self._move_enemy(task)

        # [新增 TASK04] 追击优先于手动护航，单机到达不触发团队围捕判定。
        if self.pursuit_assignments:
            self._detect_new_threats()
            self._move_own_target()
            self._step_pursuit()
            self._sync_gbsflacs_environment()
            return

        # [新增 NUM04] 取消后继续感知，但不重建守卫队形或运行围捕优化。
        if self.escort_only:
            self._detect_new_threats()
            self._move_own_target()
            self._move_platforms()
            self.phase = "手动护航：已撤退"
            self._sync_gbsflacs_environment()
            return

        if self.gbsflacs_active:

            self._move_own_target()


            # GB → CS → 多目标容量分配
            # → SFLA分别生成各目标环形航点
            self._step_gbsflacs_capture()

            # 同时判断所有目标
            self._update_gbsflacs_capture_state()

            if not self.capture_success:
                captured_count = len(
                    self.permanently_captured
                )

                total_count = len(self.threats)

                self.phase = (
                    f"GBSFLACS 多目标围捕中："
                    f"已捕获 {captured_count}/"
                    f"{total_count}"
                )

            return

        detection_changed = self._detect_new_threats()
        if detection_changed:
            self._replan_detected_guards()
        else:
            self._synchronize_guard_plan()

        self._move_own_target()
        self._move_platforms()
        self._update_control_transitions()

        detected = len(self.detected_threats)
        approaching = sum(task.state == "approaching" for task in self.threats)
        controlled = sum(task.state == "orbiting" for task in self.threats)
        if detected == 0:
            self.phase = f"正常护航：{approaching} 个远距离目标正在接近"
        elif controlled == detected:
            self.phase = f"持续动态盯防：{controlled} 个目标处于受控机动"
        else:
            self.phase = f"守卫编队形成中：已感知 {detected} 个目标"
        self._sync_gbsflacs_environment()

    def status(self) -> Dict[str, object]:
        records = []
        for target_index, task in enumerate(self.threats):
            distance = horizontal_distance(task.position, self.own_position)
            capture_indices = self._capture_members_in_range(target_index)
            records.append(
                {
                    "threat_id": task.threat_id,
                    "capture_in_range": len(capture_indices),
                    "capture_max_gap_deg": math.degrees(
                        self._maximum_capture_angular_gap(capture_indices, task.position)
                    ),
                    "state": task.state,
                    "state_label": STATE_LABELS[task.state],
                    "spawn_frame": task.spawn_frame,
                    "position": task.position.copy(),
                    "distance": distance,
                    "detected": task.state in DETECTED_STATES,
                    "motion": "随机角度分段环绕",
                    "orbit_direction": ("逆时针" if task.orbit_direction > 0 else "顺时针"),
                    "orbit_segment_angle_deg": math.degrees(task.orbit_segment_angle),
                    "orbit_segment_remaining_deg": math.degrees(task.orbit_segment_remaining),
                    "orbit_segment_count": task.orbit_segment_count,
                    "orbit_direction_changes": task.orbit_direction_changes,
                    "bearing": self.threat_bearing_label(task) if task.state != "waiting" else "未出现",
                    "guard_quota": task.guard_quota,
                    "guard_track_radius": (
                        self._guard_arc_geometry(task)[0]
                        if task.state in DETECTED_STATES else 0.0
                    ),
                    "wing_ready_ratio": self.wing_arrival_ratio(task.threat_id) if task.state in DETECTED_STATES else 0.0,
                }
            )
        # [修改 M13] 旧汇总字段保留为 T1 的兼容值；多目标 UI 使用各任务记录。
        capture_in_range = records[0]["capture_in_range"] if records else 0
        capture_max_gap_deg = records[0]["capture_max_gap_deg"] if records else 360.0
        uav_altitudes = [
            float(platform.position[2])
            for platform in self.platforms
            if platform.kind == "UAV"
        ]
        return {
            "frame": self.frame,
            "phase": self.phase,
            "message": self.last_message,
            "paused": self.paused,
            "sensor_radius": self.sensor_radius,
            "enemy_count": len(self.threats),
            "spawn_mode": self.spawn_mode,
            "detected_count": len(self.detected_threats),
            "reserve_count": len(self.reserve_guard_indices),
            "support_count": len(self.support_guard_indices),
            "normal_escort_count": sum(p.role == "escort" for p in self.platforms),
            "rotating_escort_count": len(self._rotating_escort_goals()),
            "uav_count": self.num_uav,
            "usv_count": self.num_usv,
            "gbsflacs_active": self.gbsflacs_active,
            "escort_only": self.escort_only,
            "target_count_setting": self.target_count,
            "pursuit_assignments": {self.platforms[i].identifier: f"T{t}"
                                    for i, t in self.pursuit_assignments.items()},
            "capture_success": self.capture_success,
            "capture_radius": GBSFLACS_CAPTURE_RADIUS,
            "minimum_capture_agents": MIN_CAPTURE_AGENTS,
            "capture_in_range": capture_in_range,
            "capture_max_gap_deg": capture_max_gap_deg,
            "granular_ball_count": len(self.gbsflacs_algorithm.leaf_balls),
            "cs_trigger_count": int(
                self.gbsflacs_algorithm.metrics.get("cs_trigger_count", 0)
            ),
            "sfla_waypoint_steps": int(
                self.gbsflacs_algorithm.metrics.get("sfla_waypoint_steps", 0)
            ),
            "uav_altitude_min_current": min(uav_altitudes, default=0.0),
            "uav_altitude_max_current": max(uav_altitudes, default=0.0),
            "manual_capture_assignments": {
                self.platforms[i].identifier: f"T{t}"
                for i, t in self.manual_capture_assignments.items()
            },
            "threats": records,
        }


class EscortGuardVisualizer:
    """局部与全局双三维视图，显示守卫和 GBSFLACS 三维围捕状态。"""

    FRIEND_UAV_COLOR = "#00acc1"
    FRIEND_USV_COLOR = "#43a047"
    OWN_TARGET_COLOR = "#0d47a1"
    ENEMY_COLOR = "#e53935"
    CAPTURED_COLOR = "#2e7d32"
    GBSFLACS_EDGE_COLOR = "#8e24aa"
    CORE_EDGE_COLOR = "#ffca28"
    NORMAL_EDGE_COLOR = "#1f2933"
    SEA_COLOR = "#81d4fa"

    def __init__(self, simulator: EscortGuardSimulator) -> None:
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from matplotlib.lines import Line2D
        from matplotlib.widgets import TextBox, Button  # [新增 NUM05] 指令框与即时取消按钮。

        self.plt = plt
        self.Line2D = Line2D
        self.sim = simulator
        self.animation = None

        preferred_fonts = [
            "Microsoft YaHei",
            "SimHei",
            "Noto Sans CJK SC",
            "Arial Unicode MS",
            "DejaVu Sans",
        ]
        noto_path = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
        if noto_path.exists():
            try:
                font_manager.fontManager.addfont(str(noto_path))
                preferred_fonts.insert(
                    0, font_manager.FontProperties(fname=str(noto_path)).get_name()
                )
            except (OSError, RuntimeError, ValueError):
                pass
        plt.rcParams["font.sans-serif"] = preferred_fonts
        plt.rcParams["axes.unicode_minus"] = False
        # [修改 ROTATE01] S 生成单目标；保留 Ctrl+S 保存，避免弹出默认保存对话框。
        plt.rcParams["keymap.save"] = [key for key in plt.rcParams["keymap.save"] if key.lower() != "s"]

        self.fig = plt.figure(figsize=(18, 8.8))
        if DIS_GLOBAL:
            self.local_ax = self.fig.add_subplot(1, 2, 1, projection="3d")
            self.global_ax = self.fig.add_subplot(1, 2, 2, projection="3d")
            self.global_ax.view_init(elev=90.0, azim=-90.0)
        else:
            self.local_ax = self.fig.add_subplot(1, 1, 1, projection="3d")
        self.ax = self.local_ax
        # [修改 LAYOUT02] 保留当前 1-20 编号，两行提示与绘图区分开。
        preset_items = [f"{k}={v}" for k, v in PRESET_CAPTURE_COMMANDS.items()]
        preset_rows = 2
        items_per_row = math.ceil(len(preset_items) / preset_rows)
        preset_top = 0.187
        self.fig.subplots_adjust(
            wspace=0.04, left=0.035, right=0.985, top=0.91, bottom=0.245,
        )
        # [新增 CMD04] 输入框独立于每帧清空的 3D 坐标轴；点击外部不提交指令。
        command_ax = self.fig.add_axes([0.10, 0.065, 0.82, 0.042])
        self.command_box = TextBox(command_ax, "指令 ", initial="")
        self.command_box.text_disp.set_fontsize(10)
        self.command_feedback = self.fig.text(
            0.10, 0.034, "S 随机生成单目标；M 输入完整指令或两位编号，Enter 执行，Esc 结束输入；F4 随时撤退。",
            fontsize=9, color="#263238",
        )
        # [修改 LAYOUT02] 横向两行，每行十项；删除旧五行循环和重复的注释代码。
        self.preset_texts = []
        for row in range(preset_rows):
            start = row * items_per_row
            row_text = "   |   ".join(preset_items[start:start + items_per_row])
            self.preset_texts.append(self.fig.text(
                0.05, preset_top - row * 0.034, row_text,
                fontsize=8.5, color="#37474f",
            ))
        self.fig.text(0.10, 0.117, "S：随机单目标；1-12：追击；13-18：绑定；19/G：守卫；20/J：围捕。两位编号先按 M 输入；0/C/F4 撤退。",
                      fontsize=9, color="#37474f")
        cancel_ax = self.fig.add_axes([0.83, 0.013, 0.13, 0.035])
        self.cancel_button = Button(cancel_ax, "撤退护航 (F4)", color="#ffebee", hovercolor="#ffcdd2")
        self.cancel_button.on_clicked(lambda event: self._submit_command("cancel"))
        # TextBox 先处理字符，再由本回调处理 Enter/Esc，避免 M 自身进入文本。
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self._refresh_artists()

    # [修改 M01] 删除未调用的视野采样和线条更新工具。
    def _local_limits(
            self
    ) -> Tuple[
        Tuple[float, float],
        Tuple[float, float]
    ]:

        own_x = float(
            self.sim.own_position[0]
        )

        own_y = float(
            self.sim.own_position[1]
        )

        half_width = 20.0
        half_height = 12.0

        return (
            (
                own_x - half_width,
                own_x + half_width
            ),
            (
                own_y - half_height,
                own_y + half_height
            ),
        )

    def _guard_arc_polyline(self, task: ThreatTask) -> np.ndarray:
        if task.state not in DETECTED_STATES or task.guard_quota <= 1:
            return np.empty((0, 3))
        _, direction, _ = self.sim._threat_geometry(task)
        lateral = rotate90(direction)
        radius, half_angle = self.sim._guard_arc_geometry(task)
        phis = np.linspace(-half_angle, half_angle, 90)
        return np.array(
            [
                point3(
                    horizontal(self.sim.own_position)
                    + radius
                    * (math.cos(phi) * direction + math.sin(phi) * lateral),
                    0.0,
                )
                for phi in phis
            ]
        )

    def _support_arc_polyline(self) -> np.ndarray:
        if len(self.sim.detected_threats) != 1 or not self.sim.support_guard_indices:
            return np.empty((0, 3))
        task = self.sim.detected_threats[0]
        _, threat_dir, _ = self.sim._threat_geometry(task)
        center_dir = np.array(
            [
                math.cos(self.sim.support_arc_rear_offset) * threat_dir[0]
                - math.sin(self.sim.support_arc_rear_offset) * threat_dir[1],
                math.sin(self.sim.support_arc_rear_offset) * threat_dir[0]
                + math.cos(self.sim.support_arc_rear_offset) * threat_dir[1],
            ],
            dtype=float,
        )
        lateral = rotate90(center_dir)
        phis = np.linspace(
            -self.sim.support_arc_half_angle,
            self.sim.support_arc_half_angle,
            90,
        )
        return np.array(
            [
                point3(
                    horizontal(self.sim.own_position)
                    + self.sim.support_guard_radius
                    * (math.cos(phi) * center_dir + math.sin(phi) * lateral),
                    0.0,
                )
                for phi in phis
            ]
        )

    def _draw_sea_and_boundary(
        self,
        ax,
        x_limits: Tuple[float, float],
        y_limits: Tuple[float, float],
    ) -> None:
        x0, x1 = x_limits
        y0, y1 = y_limits
        xx, yy = np.meshgrid(np.array([x0, x1]), np.array([y0, y1]))
        zz = np.zeros_like(xx)
        ax.plot_surface(
            xx,
            yy,
            zz,
            color=self.SEA_COLOR,
            alpha=0.10,
            shade=False,
            linewidth=0,
            zorder=0,
        )
        bx = [x0, x1, x1, x0, x0]
        by = [y0, y0, y1, y1, y0]
        ax.plot(bx, by, [0.0] * 5, color="#455a64", linewidth=1.3, alpha=0.75)

    def _platform_style(self, platform: Platform) -> Tuple[str, str, float, str]:
        if platform.kind == "UAV":
            face = self.FRIEND_UAV_COLOR
            marker = "o"
            base_size = 86.0
        else:
            face = self.FRIEND_USV_COLOR
            marker = "s"
            base_size = 82.0
        if platform.role == "guard":  # [修改 M13] 与实际守卫角色名称一致。
            return face, "#f9a825", base_size * 1.10, marker
        if platform.role == "support":
            return face, "#1565c0", base_size, marker
        if platform.role == "reserve":
            return face, "#2e7d32", base_size, marker
        if platform.role == "pursuit":
            return face, "#ef6c00", base_size * 1.18, marker
        if platform.role == "capture":
            return face, self.GBSFLACS_EDGE_COLOR, base_size * 1.18, marker
        return face, self.NORMAL_EDGE_COLOR, base_size, marker

    def _draw_platforms(self, ax, *, local: bool) -> None:
        size_scale = 1.0 if local else 0.55
        label_offset = 1.7 if local else 1.1
        for index, platform in enumerate(self.sim.platforms):
            x, y, z = platform.position
            face, edge, size, marker = self._platform_style(platform)
            ax.scatter(
                [x],
                [y],
                [z],
                marker=marker,
                s=size * size_scale,
                c=[face],
                edgecolors=[edge],
                linewidths=2.2 if platform.role == "core" else 1.2,
                depthshade=True,
                zorder=8,
            )
            if platform.kind == "UAV":
                ax.plot(
                    [x, x],
                    [y, y],
                    [0.0, z],
                    linestyle="--",
                    linewidth=0.9,
                    color=self.FRIEND_UAV_COLOR,
                    alpha=0.42,
                    zorder=2,
                )
                ax.scatter(
                    [x], [y], [0.0], marker=".", s=18 * size_scale,
                    c=[self.FRIEND_UAV_COLOR], alpha=0.48, zorder=3
                )
            ax.text(
                x,
                y,
                z + label_offset,
                # [新增 CMD05] 星号表示手动固定；T 编号为将执行的围捕目标。
                platform.identifier + (
                    f"/T{self.sim.manual_capture_assignments[index]}*"
                    if index in self.sim.manual_capture_assignments else
                    f"/T{self.sim.pursuit_assignments[index]}追"
                    if index in self.sim.pursuit_assignments else ""
                ),
                ha="center",
                va="bottom",
                fontsize=7.2 if local else 5.4,
                color="#263238",
                weight="bold" if platform.role == "core" else "normal",
                zorder=10,
            )

    def _draw_common_scene(self, ax, *, local: bool) -> None:
        own_x, own_y, own_z = self.sim.own_position
        ax.scatter(
            [own_x], [own_y], [own_z], marker="o", s=160 if local else 85,
            c=[self.OWN_TARGET_COLOR], edgecolors=["#082d65"], linewidths=1.6,
            depthshade=True, zorder=9
        )
        ax.text(
            own_x, own_y, own_z + 1.6,
            "高价值目标", ha="center", va="bottom",
            fontsize=8 if local else 5.8, color=self.OWN_TARGET_COLOR, weight="bold"
        )

        circle_angles = np.linspace(0.0, 2.0 * math.pi, 180)
        sensor_x = own_x + self.sim.sensor_radius * np.cos(circle_angles)
        sensor_y = own_y + self.sim.sensor_radius * np.sin(circle_angles)
        ax.plot(
            sensor_x, sensor_y, np.zeros_like(sensor_x),
            linestyle="--", linewidth=1.5, color="#1976d2", alpha=0.68,
            label="感知范围"
        )

        # [修改 M01] 删除绘图调用已被注释、结果从不使用的方向计算。
        self._draw_platforms(ax, local=local)

        for task in self.sim.spawned_threats:
            x, y, z = task.position
            captured = task.state == "captured"
            target_color = self.CAPTURED_COLOR if captured else self.ENEMY_COLOR
            target_edge = "#145a20" if captured else "#7f0000"
            ax.scatter(
                [x], [y], [z], marker="^", s=145 if local else 80,
                c=[target_color], edgecolors=[target_edge], linewidths=1.5,
                depthshade=True, zorder=9
            )
            ax.text(
                x, y, z + 1.4,
                f"敌方 T{task.threat_id}\n{STATE_LABELS[task.state]}",
                ha="center", va="bottom", fontsize=7.5 if local else 5.3,
                color=target_edge, weight="bold"
            )
            if task.state in DETECTED_STATES and not self.sim.gbsflacs_active:
                arc = self._guard_arc_polyline(task)
                if len(arc):
                    ax.plot(
                        arc[:, 0], arc[:, 1], arc[:, 2],
                        color="#f57c00", linewidth=2.0, alpha=0.82
                    )

        support_arc = self._support_arc_polyline()
        if len(support_arc):
            ax.plot(
                support_arc[:, 0], support_arc[:, 1], support_arc[:, 2],
                color="#1565c0", linestyle="-.", linewidth=1.8, alpha=0.80
            )

    def _status_lines(self) -> List[str]:
        status = self.sim.status()
        lines = [
            f"帧：{status['frame']}｜UAV/USV：{status['uav_count']}/{status['usv_count']}",
            f"阶段：{status['phase']}",
        ]
        if status["gbsflacs_active"] or status["capture_success"]:
            lines.extend(
                [
                    f"已捕获：{len(self.sim.permanently_captured)}/{status['enemy_count']}",
                    f"粒球：{status['granular_ball_count']}｜CS触发：{status['cs_trigger_count']}"
                    f"｜SFLA航点：{status['sfla_waypoint_steps']}",
                ]
            )
        else:
            lines.extend(
                [
                    f"已感知：{status['detected_count']}｜直接守卫：{sum(item['guard_quota'] for item in status['threats'])} 动态支援：{status['support_count']}｜预留：{status['reserve_count']}",
                ]
            )
        altitude_mode = "动态" if status["gbsflacs_active"] or status["capture_success"] else "护航固定"
        lines.append(
            f"UAV高度（{altitude_mode}）：{status['uav_altitude_min_current']:.1f}-"
            f"{status['uav_altitude_max_current']:.1f}｜USV：0"
        )
        for item in status["threats"]:
            if status["gbsflacs_active"] or status["capture_success"]:
                lines.append(
                    f"T{item['threat_id']} {item['state_label']}｜半径内 "
                    f"{item['capture_in_range']}/{status['minimum_capture_agents']}"
                    f"｜三维半径 ≤ {status['capture_radius']:.1f}"
                )
            elif item["state"] == "orbiting":
                lines.append(
                    f"T{item['threat_id']} {item['orbit_direction']}｜本段 {item['orbit_segment_angle_deg']:.0f}°"
                    f"｜剩余 {item['orbit_segment_remaining_deg']:.0f}°"
                )
            else:
                lines.append(f"T{item['threat_id']} {item['state_label']}｜距离 {item['distance']:.2f}")
        # [新增 CMD05] 持续显示固定关系；未列出的成员继续自动分配。
        lines.extend(self.sim.manual_capture_summary())
        lines.extend(self.sim.pursuit_summary())
        if status["rotating_escort_count"]:
            lines.append(f"随队旋转护航：{status['rotating_escort_count']} 个平台（保持原相对间隔）")
        lines.append(f"目标设置：{self.sim.target_count}；targets 1/2 可重新生成")
        if self.sim.escort_only and not self.sim.pursuit_assignments:
            lines.append("撤退护航中；19/G 恢复守卫，20/J 围捕，N 新建目标")
        lines.append(f"提示：{status['message']}")
        lines.append("运行中" if not status["paused"] else "已暂停")
        return lines

    def _configure_axis(self, ax, *, local: bool) -> None:
        ax.cla()
        if local:
            x_limits, y_limits = self._local_limits()
            title = "局部 3D 视图（跟随我方高价值目标）"
            box_aspect = (1.65, 1.05, 1.0)
        else:
            x_limits = (self.sim.world_x_min, self.sim.world_x_max)
            y_limits = (self.sim.world_y_min, self.sim.world_y_max)
            title = "全局 3D 视图（固定长方体空间）"
            box_aspect = (1.75, 1.10, 0.72)

        self._draw_sea_and_boundary(ax, x_limits, y_limits)
        self._draw_common_scene(ax, local=local)
        ax.set_xlim(*x_limits)
        ax.set_ylim(*y_limits)
        ax.set_zlim(VIEW_Z_MIN, VIEW_Z_MAX)
        ax.set_xlabel("X / 海里（示意）", labelpad=8)
        ax.set_ylabel("Y / 海里（示意）", labelpad=8)
        ax.set_zlabel("高度 Z", labelpad=7)
        ax.set_title(title, fontsize=12.5, weight="bold", pad=14)
        ax.set_box_aspect(box_aspect)
        ax.grid(True, alpha=0.28, linestyle="--")

        status_text = "\n".join(self._status_lines()) if local else (
            "固定全局空间\n"
            f"X：[{self.sim.world_x_min:.0f}, {self.sim.world_x_max:.0f}]\n"
            f"Y：[{self.sim.world_y_min:.0f}, {self.sim.world_y_max:.0f}]\n"
            f"Z：[{VIEW_Z_MIN:.0f}, {VIEW_Z_MAX:.0f}]\n"
            "S：随机单目标；N：按设置生成\n"
            "J：启动 GBSFLACS 三维围捕\n"
            "M：输入编号 1-20 或完整指令\n"
            "0/C/F4：取消并返回护航"
        )
        ax.text2D(
            0.985, 0.985, status_text,
            transform=ax.transAxes, ha="right", va="top", fontsize=8.2,
            bbox=dict(facecolor="white", alpha=0.88, edgecolor="#78909c", boxstyle="round,pad=0.42")
        )
        ax.text2D(
            0.015, 0.015,
            "S：单目标  N：生成  G：守卫  J：围捕  F4/0：撤退  M：指令  空格：暂停",
            transform=ax.transAxes, ha="left", va="bottom", fontsize=8.0,
            bbox=dict(facecolor="white", alpha=0.80, edgecolor="#90a4ae")
        )

        legend_handles = [
            self.Line2D([0], [0], marker="o", color="none", markerfacecolor=self.FRIEND_UAV_COLOR,
                        markeredgecolor="#ef6c00", markeredgewidth=2, markersize=8, label="单平台追击"),
            self.Line2D([0], [0], marker="o", color="none", markerfacecolor=self.FRIEND_UAV_COLOR,
                        markeredgecolor=self.NORMAL_EDGE_COLOR, markersize=8, label="我方 UAV"),
            self.Line2D([0], [0], marker="s", color="none", markerfacecolor=self.FRIEND_USV_COLOR,
                        markeredgecolor=self.NORMAL_EDGE_COLOR, markersize=8, label="我方 USV"),
            self.Line2D([0], [0], marker="D", color="none", markerfacecolor=self.OWN_TARGET_COLOR,
                        markeredgecolor="#082d65", markersize=8, label="高价值目标"),
            self.Line2D([0], [0], marker="^", color="none", markerfacecolor=self.ENEMY_COLOR,
                        markeredgecolor="#7f0000", markersize=8, label="敌方"),
            self.Line2D(
                [0],
                [0],
                marker="o",
                color="none",
                markerfacecolor=self.FRIEND_UAV_COLOR,
                markeredgecolor="#f9a825",
                markeredgewidth=2,
                markersize=8,
                label="分层守卫",
            ),
            self.Line2D([0], [0], marker="o", color="none", markerfacecolor=self.FRIEND_UAV_COLOR,
                        markeredgecolor=self.GBSFLACS_EDGE_COLOR, markeredgewidth=2, markersize=8, label="GBSFLACS围捕"),
            self.Line2D([0], [0], marker="^", color="none", markerfacecolor=self.CAPTURED_COLOR,
                        markeredgecolor="#145a20", markersize=8, label="围捕成功目标"),
        ]
        ax.legend(handles=legend_handles, loc="lower right", fontsize=7.5, framealpha=0.9)

    def _refresh_artists(self) -> Tuple[object, ...]:
        self._configure_axis(self.local_ax, local=True)
        if DIS_GLOBAL:
            self._configure_axis(self.global_ax, local=False)
        self.fig.suptitle(
            "护航守卫 + GBSFLACS 三维多目标围捕仿真",
            fontsize=15,
            weight="bold",
        )
        return tuple()

    def update(self, _frame: int) -> Tuple[object, ...]:
        # [新增 CMD04] 编辑指令时不推进仿真，也不改变用户原来的暂停状态。
        if not self.command_box.capturekeystrokes:
            self.sim.step()
        return self._refresh_artists()

    # [新增 CMD04] 指令执行反馈与仿真状态分开，避免下一帧覆盖错误原因。
    def _submit_command(self, text: str) -> None:
        try:
            message = self.sim.execute_command(text)
        except ValueError as exc:
            self.command_feedback.set_text(f"指令未执行：{exc}")
            self.command_feedback.set_color("#b71c1c")
        else:
            self.command_feedback.set_text(message)
            self.command_feedback.set_color("#1b5e20")
            self.command_box.stop_typing()
            self.command_box.set_val("")
        self._refresh_artists()
        self.fig.canvas.draw_idle()

    def on_key(self, event) -> None:
        key = (event.key or "").lower()
        # [新增 NUM05] F4 无论正在输入还是暂停均可立即取消状态。
        if key == "f4":
            self._submit_command("cancel")
            return
        # [新增 CMD04] 输入中的 n/j/空格只作文本，不能生成目标或提前启动围捕。
        if self.command_box.capturekeystrokes:
            if key in {"escape", "esc"}:
                self.command_box.stop_typing()
                self.command_box.set_val("")
                self.command_feedback.set_text("已取消输入，固定分配保持不变")
                self.command_feedback.set_color("#263238")
            elif key in {"enter", "return"}:
                self._submit_command(self.command_box.text)
            self.fig.canvas.draw_idle()
            return
        # [修改 TASK06] 非编辑状态 1-9 可直接按；两位编号必须在输入框提交。
        if key in PRESET_CAPTURE_COMMANDS or key in {"0", "c"}:
            self._submit_command(key)
            return
        if key == "s":  # [修改 ROTATE01] 等价于 targets 1，立即随机生成一个目标。
            self._submit_command("targets 1")
            return
        if key == "g":
            self._submit_command("guard")
            return
        if key == "m":
            self.command_box.begin_typing()
            self.fig.canvas.draw_idle()
            return
        if key in {" ", "space"}:
            self.sim.toggle_pause()
        elif key == "n":
            self.sim.reset()
            self.command_feedback.set_text("已生成新目标并清除旧固定分配；可输入 bind 指定成员")
            self.command_feedback.set_color("#263238")
        elif key == "j":
            self.sim.start_gbsflacs_capture()
        elif key in {"escape", "esc"}:
            self.plt.close(self.fig)
            return
        else:
            return
        self._refresh_artists()
        self.fig.canvas.draw_idle()

    def show(self, interval_ms: int = 55) -> None:
        from matplotlib.animation import FuncAnimation

        self.animation = FuncAnimation(
            self.fig,
            self.update,
            interval=interval_ms,
            blit=False,
            cache_frame_data=False,
        )
        self.plt.show()

    def save_snapshot(self, path: Union[str, Path], dpi: int = 150) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        self._refresh_artists()
        self.fig.savefig(output, dpi=dpi, bbox_inches="tight")
        return output

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sensor-radius", type=float, default=DEFAULT_SENSOR_RADIUS)
    parser.add_argument("--reserve-count", type=int, choices=(0, 2), default=ESCORT_RESERVE_COUNT, help="是否从剩余平台中保留 2 个贴身守卫；每目标三人优先")
    parser.add_argument("--target-count", type=int, choices=(1, 2), default=TARGETS_NUM,
                        help="N 键生成的目标数量；运行中 targets 1/2 可切换")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--headless-frames", type=int, default=0, metavar="N")
    parser.add_argument("--save-snapshot", type=str, default=None, metavar="PATH")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.headless_frames < 0:
        raise SystemExit("--headless-frames must be non-negative")
    if args.headless_frames or args.save_snapshot:
        import matplotlib
        matplotlib.use("Agg")
    sim = EscortGuardSimulator(
        sensor_radius=args.sensor_radius,
        seed=args.seed,
        target_count=args.target_count,
        num_uav=NUM_UAV,
        num_usv=NUM_USV,
        escort_reserve_count=args.reserve_count,
    )
    for _ in range(args.headless_frames):
        sim.step()
    if args.save_snapshot:
        visualizer = EscortGuardVisualizer(sim)
        output = visualizer.save_snapshot(args.save_snapshot)
        print(f"Snapshot saved: {output.resolve()}")
        visualizer.plt.close(visualizer.fig)
        # [修改 M12] 仅保存快照时直接退出，不再创建第二个窗口。
        if not args.headless_frames:
            return 0
    if args.headless_frames:
        status = sim.status()
        print(
            "Headless dynamic-threat run completed: "
            f"frames={status['frame']}, enemies={status['enemy_count']}, "
            f"detected={status['detected_count']}, phase={status['phase']}, reserve={args.reserve_count}"
        )
        return 0
    print(
        f"UAV={NUM_UAV}, USV={NUM_USV}; 初始无目标，按 S 生成 1 个，N 按当前设置生成（初始 {args.target_count} 个）；"
        f"随机环绕角度={RANDOM_ORBIT_MIN_ANGLE_DEG:.0f}°~{RANDOM_ORBIT_MAX_ANGLE_DEG:.0f}°；"
        f"M 输入 targets 1/2、chase U1 T1 或 bind T1 U1 S1；20/J 围捕；0/C/F4 撤退；"
        f"感知半径={args.sensor_radius}; 围捕半径={GBSFLACS_CAPTURE_RADIUS}; "
        f"reserve_count={args.reserve_count}; "
        f"全局画布=[{WORLD_X_MIN},{WORLD_X_MAX}]x[{WORLD_Y_MIN},{WORLD_Y_MAX}]."
    )
    EscortGuardVisualizer(sim).show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
