"""Small JSON contract shared by the web process and isolated robot worker."""
import math


def validate_parameters(value):
    if not isinstance(value, dict) or set(value) != {"object_start", "target"}:
        raise ValueError("必须提供 object_start 和 target，不能提交其他参数。")
    for section, keys in (("object_start", {"x", "y", "yaw_deg"}), ("target", {"x", "y"})):
        fields = value[section]
        if not isinstance(fields, dict) or set(fields) != keys:
            raise ValueError(section + " 的字段不完整或包含未知字段。")
        for number in fields.values():
            if type(number) not in (int, float) or not math.isfinite(number):
                raise ValueError("坐标和偏航角必须是有限数字，不能留空或使用字符串。")
    return value


def result_template(job_id, run_id=None):
    return dict(status="failed", job_id=job_id, run_id=run_id, error=None,
                perception_success=False, grasp_execution_completed=False,
                place_execution_completed=False, visual_task_success=False,
                steps=[], position_error_mm=None, orientation_error_deg=None,
                orientation_error_reason="现有视觉复核未报告独立姿态误差；不以零值替代。",
                result_json_url=None, video_url=None, video_available=False)
