"""Build the private E1 manifest from the frozen 60-item recording plan."""
import argparse
import csv
import hashlib
import json
import pathlib
from datetime import datetime, timezone


QUIET = [
    "启动无人机与无人艇协同搜索任务", "暂停当前任务", "恢复当前任务", "停止当前任务",
    "请开始执行海面巡检", "让所有设备保持当前位置", "查看当前任务运行状态", "报告无人机剩余电量",
    "报告无人艇当前航速", "将搜索区域扩大到东侧海域", "保持现有编队继续前进", "所有设备返回初始位置",
    "请重新规划当前航线", "降低无人机飞行速度", "提高无人艇巡航速度", "开始执行目标跟踪",
    "暂停目标跟踪并等待", "继续执行协同编队任务", "终止本次仿真运行", "检查所有设备连接状态",
    "读取最新一帧仿真结果", "显示当前场景中的设备数量", "请确认任务是否已经开始", "请确认任务是否已经暂停",
    "请确认任务是否已经停止", "不要启动新的任务", "不要暂停正在运行的任务", "不要停止当前任务",
    "先暂停任务然后等待确认", "只显示识别文字不要执行动作", "这是一段本地语音识别测试", "请把我说的话转换成文字",
    "今天进行无人系统联合演示", "当前环境没有明显背景噪声", "请记录这一段完整中文语音", "识别完成后允许人工修改文字",
    "页面刷新后不要自动重复识别", "退出登录后清除旧的识别结果", "本地模型不应连接云端服务", "语音识别不依赖仿真场景就绪",
]

NOISE = [
    "在风扇噪声下启动协同搜索", "在键盘声中暂停当前任务", "在走廊说话声中恢复当前任务",
    "在车辆背景声下停止当前任务", "有海浪噪声时报告无人艇位置", "有空调噪声时报告无人机高度",
    "在多人交谈背景下保持当前编队", "在音乐背景下重新规划航线", "在开门关门声中查看任务状态",
    "在持续白噪声下只显示识别文字",
]

TERMINOLOGY = [
    ("使用改进蛙跳算法计算协同航迹", ["改进蛙跳算法", "协同航迹"]),
    ("检查无人机和无人艇的运行时引用", ["无人机", "无人艇", "运行时引用"]),
    ("核对运行代次与展示绑定标识", ["运行代次", "展示绑定"]),
    ("执行粒子群优化并输出全局最优解", ["粒子群优化", "全局最优解"]),
    ("验证航向角速度与经纬度遥测数据", ["航向角速度", "经纬度", "遥测"]),
    ("检查编队控制和动态避障是否正常", ["编队控制", "动态避障"]),
    ("确认场景就绪挑战与帧应用回执", ["场景就绪", "帧应用回执"]),
    ("验证幂等请求和命令序列连续性", ["幂等请求", "命令序列"]),
    ("检查WebGL画面与算法状态是否一致", ["WebGL", "算法状态"]),
    ("记录实时因子峰值内存和九十五分位延迟", ["实时因子", "峰值内存", "九十五分位"]),
]

EXTENSIONS = (".wav", ".webm", ".ogg", ".m4a", ".mp4", ".mp3")


def plan():
    rows = []
    items = [(text, "quiet", []) for text in QUIET]
    items += [(text, "noise", []) for text in NOISE]
    items += [(text, "terminology", terms) for text, terms in TERMINOLOGY]
    for index, (reference, group, terms) in enumerate(items, 1):
        rows.append({
            "id": f"e1-{index:03d}", "speakerId": f"speaker-{chr(97 + (index - 1) % 3)}",
            "group": group, "reference": reference, "terms": terms,
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-csv", help="write the shareable recording task table")
    parser.add_argument("--plan-json", help="write the browser recording task data")
    parser.add_argument("--audio-dir", help="private directory containing e1-001..e1-060 audio")
    parser.add_argument("--manifest", help="write the private frozen manifest")
    args = parser.parse_args()
    rows = plan()
    if args.plan_csv:
        destination = pathlib.Path(args.plan_csv)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("id", "speakerId", "group", "reference", "terms"))
            writer.writeheader()
            for row in rows:
                writer.writerow({**row, "terms": "|".join(row["terms"])})
    if args.plan_json:
        destination = pathlib.Path(args.plan_json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.audio_dir or args.manifest:
        if not args.audio_dir or not args.manifest:
            raise SystemExit("--audio-dir and --manifest must be supplied together")
        root = pathlib.Path(args.audio_dir).resolve(strict=True)
        samples, missing = [], []
        for row in rows:
            matches = [root / (row["id"] + extension) for extension in EXTENSIONS]
            matches = [path for path in matches if path.is_file()]
            if len(matches) != 1:
                missing.append({"id": row["id"], "matches": len(matches)})
                continue
            audio = matches[0]
            samples.append({
                **row, "path": audio.as_posix(),
                "audioSha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            })
        if missing:
            raise SystemExit("Missing or ambiguous recordings: " + json.dumps(missing, ensure_ascii=False))
        manifest = {
            "datasetId": "e1-zh-60-v1",
            "frozenAt": datetime.now(timezone.utc).isoformat(),
            "normalizationRevision": "nfkc-lower-remove-punctuation-and-separators-v1",
            "samples": samples,
        }
        pathlib.Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"datasetId": manifest["datasetId"], "samples": len(samples)}))
    elif not args.plan_csv and not args.plan_json:
        parser.error("provide --plan-csv, --plan-json, or the private manifest arguments")


if __name__ == "__main__":
    main()
