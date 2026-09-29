"""Tạo task demo trên CVAT có đầy đủ lỗi **Level 1** (Overall / Completeness Check).

Script sinh 12 ảnh PNG 640x360 (Pillow - đi kèm ``cvat-sdk``) rồi tạo một task trên
CVAT **và nạp luôn pre-annotation cố ý sai**, sao cho mỗi mục Level 1 của proposal
đều được kích hoạt. Nhờ đó reviewer thấy ngay kết quả QA/QC trên chính CVAT.

Bản đồ lỗi (khớp ``rules/level1_v1.yaml``):

==========  ==========================================================
Frame       Lỗi cố ý tạo -> rule
==========  ==========================================================
0           xe + biển số hợp lệ (tham chiếu, không lỗi)
1           xe hợp lệ
2, 3, 4     không có annotation -> ``empty_frame`` + ``empty_frame_range``
5           xe thiếu attribute ``color`` -> ``required_attributes``
6           chỉ có ``pedestrian``, thiếu ``vehicle`` -> ``missing_label``
7           hai xe trùng nhau (IoU ~0.89) -> ``duplicate_bbox``
8           nhãn ``forklift`` lạ -> ``unexpected_label``
9           12 xe trong một frame -> ``object_count`` (quá nhiều)
10, 11      xe hợp lệ
==========  ==========================================================

Cách dùng (chạy trong thư mục repo, cần ``.env`` có ``CVAT_HOST`` + ``CVAT_TOKEN``):

    python scripts/create_demo_task.py
    python scripts/create_demo_task.py --name "QAQC Level 1 demo" --keep-frames

Sau khi có task id, chạy:

    python -m qaqc run <task_id> --rules rules/level1_v1.yaml -o reports/level1.json
    python -m qaqc publish <task_id> --rules rules/level1_v1.yaml --publish-severity warning

Rồi mở ``http://localhost:8080/tasks/<task_id>`` (hoặc tab *Quality control*) để xem
từng lỗi và nhảy tới frame tương ứng.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

if __package__ in (None, ""):  # chạy trực tiếp: python scripts/create_demo_task.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from qaqc.config import CVATConfig
from qaqc.legacy import configure_logging

#: Kích thước ảnh demo (px).
FRAME_WIDTH = 640
FRAME_HEIGHT = 360

#: Số frame của task demo.
FRAME_COUNT = 12

#: Tên task mặc định.
DEFAULT_NAME = "QAQC Level 1 demo"

#: Thư mục chứa ảnh sinh ra (``reports/`` đã nằm trong .gitignore).
DEFAULT_FRAMES_DIR = Path("reports") / "demo_frames"

#: Tên format import của CVAT (lấy từ ``GET /api/server/annotation/formats``).
ANNOTATION_FORMAT = "CVAT 1.1"

#: Màu vẽ theo nhãn (chỉ để ảnh demo dễ nhìn).
LABEL_COLORS: dict[str, tuple[int, int, int]] = {
    "vehicle": (200, 60, 40),
    "pedestrian": (40, 120, 200),
    "license_plate": (240, 220, 60),
    "forklift": (110, 190, 60),
}

#: Label + attribute của task demo (phải khớp cấu hình trong ``rules/level1_v1.yaml``).
DEMO_LABELS: tuple[dict[str, Any], ...] = (
    {
        "name": "vehicle",
        "color": "#c83c28",
        "attributes": (
            {
                "name": "vehicle_type",
                "input_type": "select",
                "mutable": False,
                "values": ("car", "truck", "bus"),
                "default_value": "car",
            },
            {
                "name": "color",
                "input_type": "select",
                "mutable": False,
                "values": ("white", "black", "red"),
                # Cố ý KHÔNG đặt default_value: nếu đặt, CVAT sẽ tự điền giá trị mặc
                # định khi import và lỗi "thiếu attribute" ở frame 5 không còn xuất hiện.
            },
        ),
    },
    {
        "name": "license_plate",
        "color": "#f0dc3c",
        "attributes": ({"name": "plate_number", "input_type": "text", "mutable": False},),
    },
    {"name": "pedestrian", "color": "#2878c8"},
    {"name": "forklift", "color": "#6ebe3c"},
)


@dataclass(frozen=True)
class DemoObject:
    """Một object demo: được vẽ lên ảnh và ghi vào pre-annotation."""

    label: str
    #: Bounding box ``(x1, y1, x2, y2)`` theo pixel ảnh.
    bbox: tuple[int, int, int, int]
    attributes: tuple[tuple[str, str], ...] = ()


#: Attribute đầy đủ của một ``vehicle`` hợp lệ.
VEHICLE_ATTRS: tuple[tuple[str, str], ...] = (
    ("vehicle_type", "car"),
    ("color", "white"),
)


def _grid_vehicles(count: int) -> list[DemoObject]:
    """``count`` xe xếp thành lưới trong vùng đường (frame 9 - quá nhiều object)."""
    vehicles: list[DemoObject] = []
    for index in range(count):
        row, column = divmod(index, 6)
        x = 20 + column * 100
        y = 250 + row * 50
        vehicles.append(DemoObject("vehicle", (x, y, x + 60, y + 40), VEHICLE_ATTRS))
    return vehicles


def demo_objects() -> dict[int, list[DemoObject]]:
    """Object của từng frame (xem bảng lỗi trong docstring của module)."""
    return {
        0: [
            DemoObject("vehicle", (100, 240, 180, 300), VEHICLE_ATTRS),
            DemoObject("license_plate", (115, 258, 150, 278), (("plate_number", "51A-12345"),)),
        ],
        1: [DemoObject("vehicle", (300, 240, 380, 300), VEHICLE_ATTRS)],
        # Frame 2, 3, 4: cố ý KHÔNG có annotation -> empty_frame / empty_frame_range.
        5: [
            # Thiếu attribute 'color' -> required_attributes.
            DemoObject("vehicle", (120, 240, 200, 300), (("vehicle_type", "truck"),)),
        ],
        6: [
            # Chỉ có pedestrian -> missing_label (frame thiếu vehicle).
            DemoObject("pedestrian", (420, 215, 450, 300)),
        ],
        7: [
            # Hai box gần như trùng khít -> duplicate_bbox (IoU ~0.89).
            DemoObject("vehicle", (200, 240, 280, 300), VEHICLE_ATTRS),
            DemoObject("vehicle", (202, 242, 282, 302), VEHICLE_ATTRS),
        ],
        8: [
            DemoObject("vehicle", (60, 240, 140, 300), VEHICLE_ATTRS),
            # Nhãn không nằm trong allowed_labels -> unexpected_label.
            DemoObject("forklift", (480, 235, 560, 300)),
        ],
        9: _grid_vehicles(12),  # Quá nhiều object -> object_count.
        10: [DemoObject("vehicle", (250, 240, 330, 300), VEHICLE_ATTRS)],
        11: [DemoObject("vehicle", (350, 240, 430, 300), VEHICLE_ATTRS)],
    }


# ---------------------------------------------------------------------------
# Sinh ảnh demo
# ---------------------------------------------------------------------------
def render_frames(directory: Path, objects: dict[int, list[DemoObject]]) -> list[Path]:
    """Sinh ``FRAME_COUNT`` ảnh PNG (nền đường + object của từng frame).

    :param directory: thư mục ghi ảnh (được tạo nếu chưa có).
    :returns: danh sách đường dẫn ảnh theo thứ tự frame.
    """
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for frame in range(FRAME_COUNT):
        image = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (135, 206, 235))
        draw = ImageDraw.Draw(image)

        road_top = FRAME_HEIGHT * 2 // 3
        draw.rectangle((0, road_top, FRAME_WIDTH, FRAME_HEIGHT), fill=(88, 88, 92))
        for x in range(0, FRAME_WIDTH, 80):  # vạch chia làn cho dễ nhìn
            draw.line(
                ((x, FRAME_HEIGHT - 12), (x + 40, FRAME_HEIGHT - 12)),
                fill=(250, 250, 250),
                width=3,
            )
        draw.text((8, 8), f"frame {frame}", fill=(30, 30, 30))

        for obj in objects.get(frame, []):
            x1, y1, x2, y2 = obj.bbox
            color = LABEL_COLORS.get(obj.label, (160, 160, 160))
            draw.rectangle((x1, y1, x2, y2), fill=color, outline=(20, 20, 20), width=2)
            draw.text((x1 + 4, y1 + 4), obj.label, fill=(255, 255, 255))

        path = directory / f"frame_{frame:03d}.png"
        image.save(path)
        paths.append(path)

    return paths


# ---------------------------------------------------------------------------
# Pre-annotation (CVAT 1.1 XML)
# ---------------------------------------------------------------------------
def _label_element(label: dict[str, Any]) -> ET.Element:
    """``<label>`` trong phần ``<meta>`` (giống file export của CVAT)."""
    element = ET.Element("label")
    ET.SubElement(element, "name").text = str(label["name"])
    ET.SubElement(element, "color").text = str(label.get("color", "#000000"))
    ET.SubElement(element, "type").text = "any"

    attributes = label.get("attributes") or ()
    if not attributes:
        return element

    attributes_element = ET.SubElement(element, "attributes")
    for attribute in attributes:
        attribute_element = ET.SubElement(attributes_element, "attribute")
        ET.SubElement(attribute_element, "name").text = str(attribute["name"])
        ET.SubElement(attribute_element, "mutable").text = str(
            bool(attribute.get("mutable", False))
        )
        ET.SubElement(attribute_element, "input_type").text = str(
            attribute.get("input_type", "text")
        )
        if attribute.get("default_value") is not None:
            ET.SubElement(attribute_element, "default_value").text = str(attribute["default_value"])
        values = attribute.get("values") or ()
        if values:
            ET.SubElement(attribute_element, "values").text = "\n".join(str(v) for v in values)

    return element


def build_annotation_xml(objects: dict[int, list[DemoObject]], name: str) -> str:
    """Sinh nội dung file pre-annotation theo định dạng CVAT 1.1.

    Cấu trúc giống hệt file export của CVAT (``<meta>`` + ``<image>``) để importer
    nhận đúng nhãn/attribute; frame được đánh số bằng ``<image id="N">``.
    """
    root = ET.Element("annotations")
    ET.SubElement(root, "version").text = "1.1"

    meta = ET.SubElement(root, "meta")
    task = ET.SubElement(meta, "task")
    ET.SubElement(task, "id").text = "0"
    ET.SubElement(task, "name").text = name
    ET.SubElement(task, "size").text = str(FRAME_COUNT)
    ET.SubElement(task, "mode").text = "annotation"
    labels_element = ET.SubElement(task, "labels")
    for label in DEMO_LABELS:
        labels_element.append(_label_element(label))

    for frame in range(FRAME_COUNT):
        image_element = ET.SubElement(root, "image")
        image_element.set("id", str(frame))
        image_element.set("name", f"frame_{frame:03d}.png")
        image_element.set("width", str(FRAME_WIDTH))
        image_element.set("height", str(FRAME_HEIGHT))

        for obj in objects.get(frame, []):
            x1, y1, x2, y2 = obj.bbox
            box = ET.SubElement(image_element, "box")
            box.set("label", obj.label)
            box.set("source", "manual")
            box.set("occluded", "0")
            box.set("xtl", f"{x1:.2f}")
            box.set("ytl", f"{y1:.2f}")
            box.set("xbr", f"{x2:.2f}")
            box.set("ybr", f"{y2:.2f}")
            box.set("z_order", "0")
            for attribute_name, attribute_value in obj.attributes:
                attribute_element = ET.SubElement(box, "attribute")
                attribute_element.set("name", attribute_name)
                attribute_element.text = attribute_value

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ---------------------------------------------------------------------------
# Tạo task trên CVAT
# ---------------------------------------------------------------------------
def _label_spec(label: dict[str, Any]) -> dict[str, Any]:
    """``labels`` của ``TaskWriteRequest`` (bỏ khoá ``None``, đổi tuple -> list).

    SDK validate kiểu dữ liệu khá chặt: ``values`` bắt buộc là ``list`` nên phải
    chuyển từ tuple trong :data:`DEMO_LABELS`.
    """
    spec: dict[str, Any] = {"name": label["name"], "color": label.get("color", "#000000")}
    attributes = label.get("attributes") or ()
    if attributes:
        spec["attributes"] = []
        for attribute in attributes:
            item: dict[str, Any] = {
                key: value for key, value in attribute.items() if value is not None
            }
            # `AttributeRequest` của SDK yêu cầu khoá 'values' (để rỗng với input_type=text).
            item["values"] = list(item.get("values") or [])
            spec["attributes"].append(item)
    return spec


def create_demo_task(client: Any, name: str, frames: list[Path], xml_path: Path) -> Any:
    """Tạo task từ ảnh + nạp pre-annotation trong **một** lời gọi SDK.

    ``create_from_data`` nhận ``annotation_path``/``annotation_format`` nên không cần
    gọi thêm ``import_annotations``.
    """
    spec: dict[str, Any] = {
        "name": name,
        "labels": [_label_spec(label) for label in DEMO_LABELS],
    }
    return client.tasks.create_from_data(
        spec,
        resources=[str(path) for path in frames],
        data_params={"image_quality": 75},
        annotation_path=str(xml_path),
        annotation_format=ANNOTATION_FORMAT,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Tham số dòng lệnh của script."""
    parser = argparse.ArgumentParser(
        prog="python scripts/create_demo_task.py",
        description="Tạo task demo trên CVAT với đầy đủ lỗi Level 1 (Overall/Completeness).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--name", default=DEFAULT_NAME, help="Tên task tạo trên CVAT")
    parser.add_argument(
        "--frames-dir", default=str(DEFAULT_FRAMES_DIR), help="Thư mục chứa ảnh demo"
    )
    parser.add_argument("--env-file", default=".env", help="File .env chứa thông tin CVAT")
    parser.add_argument("--host", help="URL CVAT server (mặc định: CVAT_HOST)")
    parser.add_argument("--user", help="Tài khoản CVAT (mặc định: CVAT_USER)")
    parser.add_argument("--password", help="Mật khẩu CVAT (mặc định: CVAT_PASS)")
    parser.add_argument("--token", help="Access token CVAT (mặc định: CVAT_TOKEN)")
    parser.add_argument(
        "--keep-frames", action="store_true", help="Giữ lại ảnh demo sau khi tạo task"
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Tăng mức độ log (-v: INFO)"
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    """Sinh ảnh -> tạo task trên CVAT -> in hướng dẫn chạy QA/QC."""
    args = _parse_args(argv)
    if args.verbose:
        configure_logging(args.verbose)

    frames_dir = Path(args.frames_dir)
    objects = demo_objects()

    print(f"1/3 Sinh {FRAME_COUNT} ảnh demo vào {frames_dir} ...", flush=True)
    frames = render_frames(frames_dir, objects)
    xml_path = frames_dir / "annotations.xml"
    xml_path.write_text(build_annotation_xml(objects, args.name), encoding="utf-8")

    print("2/3 Kết nối CVAT và tạo task (có thể mất vài giây) ...", flush=True)
    config = CVATConfig.from_env(
        env_file=args.env_file,
        host=args.host,
        user=args.user,
        password=args.password,
        token=args.token,
    )
    client = config.create_client()
    try:
        task = create_demo_task(client, args.name, frames, xml_path)
        jobs = task.get_jobs()
    finally:
        client.close()

    job_list = ", ".join(f"#{job.id} (frame {job.start_frame}-{job.stop_frame})" for job in jobs)
    print(f"\n3/3 Đã tạo task #{task.id}: {task.name}")
    print(f"  - Số frame     : {task.size}")
    print(f"  - Job          : {job_list or '(chưa có)'}")
    print(f"  - Mở task      : {config.host}/tasks/{task.id}")
    print(f"  - Quality ctrl : {config.host}/tasks/{task.id}/quality-control")
    print("\nBước tiếp theo:")
    print(
        f"  python -m qaqc run {task.id} --rules rules/level1_v1.yaml "
        f"-o reports/level1_task_{task.id}.json"
    )
    print(
        f"  python -m qaqc publish {task.id} --rules rules/level1_v1.yaml "
        f"--publish-severity warning"
    )

    if not args.keep_frames:
        for path in (*frames, xml_path):
            path.unlink(missing_ok=True)
        print(f"\nĐã xoá ảnh tạm trong {frames_dir} (dùng --keep-frames để giữ lại).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
