"""Command line entry point for the parttrack batch pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .audio import check_tools, duration_seconds, encode, render_wav
from .config import ProjectConfig
from .metadata import build_metadata, write_metadata
from .mixdown import MixSpec, count_in_seconds, plan_mixes, resolve_levels, write_mix
from .pianoroll import PianoRollRenderer
from .rehearsal import build_kit
from .score import Score, describe, load_score


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def cmd_doctor(_: argparse.Namespace) -> int:
    report = check_tools()
    _log(f"parttrack {__version__}")
    missing = False
    for name, value in report.items():
        status = "ok " if not value.startswith("MISSING") else "MISS"
        if value.startswith("MISSING"):
            missing = True
        _log(f"  [{status}] {name:<10} {value}")
    if missing:
        _log(
            "\nInstall the missing pieces, e.g.:\n"
            "  apt-get install -y ffmpeg fluidsynth fluid-soundfont-gm"
        )
        return 1
    _log("\nAll external dependencies available.")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    project = ProjectConfig.load(args.project)
    score = load_score(project)
    _log(f"project: {project.title}  (source: {project.source_path})")
    _log(describe(score))
    specs = plan_mixes(project)
    _log(f"\nplanned deliverables: {len(specs)}")
    for spec in specs:
        _log(f"  - {spec.slug}")
    return 0


def _build_one(
    project: ProjectConfig,
    score: Score,
    spec: MixSpec,
    out_dir: Path,
    make_video: bool,
) -> dict:
    started = time.time()
    midi_path = write_mix(project, score, spec, out_dir / "midi" / f"{spec.slug}.mid")

    wav_path = out_dir / "wav" / f"{spec.slug}.wav"
    render_wav(
        midi_path,
        wav_path,
        sample_rate=project.render.sample_rate,
        gain=project.render.gain,
    )

    audio_format = project.outputs.audio_format
    audio_path = out_dir / "audio" / f"{spec.slug}.{audio_format}"
    encode(wav_path, audio_path, audio_format)

    count_in_s = count_in_seconds(project, score, spec)
    metadata = build_metadata(project, score, spec, count_in_s)
    metadata_path = write_metadata(
        metadata, out_dir / "metadata" / f"{spec.slug}.json"
    )

    record = {
        "slug": spec.slug,
        "variant": spec.variant,
        "part": spec.lead_part_id,
        "tempo_label": spec.tempo_label,
        "tempo_scale": spec.tempo_scale,
        "midi": str(midi_path.relative_to(out_dir)),
        "audio": str(audio_path.relative_to(out_dir)),
        "metadata": str(metadata_path.relative_to(out_dir)),
        "title": metadata.title,
    }

    if make_video and project.video.enabled:
        duration = duration_seconds(wav_path)
        renderer = PianoRollRenderer(
            project=project,
            score=score,
            spec=spec,
            levels=resolve_levels(project, spec),
            count_in_s=count_in_s,
            duration_s=duration,
        )
        video_path = renderer.render(wav_path, out_dir / "video" / f"{spec.slug}.mp4")
        record["video"] = str(video_path.relative_to(out_dir))
        record["duration_s"] = round(duration, 2)

    if not project.outputs.keep_wav:
        wav_path.unlink(missing_ok=True)

    record["elapsed_s"] = round(time.time() - started, 1)
    return record


def cmd_build(args: argparse.Namespace) -> int:
    project = ProjectConfig.load(args.project)
    if args.out:
        project.out_dir = Path(args.out)
    score = load_score(project)

    specs = plan_mixes(project)
    if args.only:
        wanted = set(args.only)
        specs = [spec for spec in specs if spec.slug in wanted or spec.lead_part_id in wanted]
        if not specs:
            _log(f"no deliverable matches {sorted(wanted)}")
            return 1
    if args.limit:
        specs = specs[: args.limit]

    out_dir = project.output_path
    _log(f"project     : {project.title}")
    _log(f"source      : {project.source_path}")
    _log(f"output      : {out_dir}")
    _log(f"deliverables: {len(specs)}")

    if args.dry_run:
        for spec in specs:
            _log(f"  would build {spec.slug}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for index, spec in enumerate(specs, start=1):
        _log(f"[{index}/{len(specs)}] {spec.slug} ...")
        try:
            record = _build_one(project, score, spec, out_dir, make_video=not args.no_video)
        except Exception as error:  # noqa: BLE001 - one bad mix must not sink the batch
            _log(f"    FAILED: {error}")
            records.append({"slug": spec.slug, "error": str(error)})
            if args.fail_fast:
                _write_index(project, out_dir, records)
                return 1
            continue
        suffix = f" -> {record.get('video', record['audio'])}"
        _log(f"    done in {record['elapsed_s']}s{suffix}")
        records.append(record)

    _write_index(project, out_dir, records)
    failures = [record for record in records if "error" in record]
    _log(
        f"\n{len(records) - len(failures)}/{len(records)} deliverables built "
        f"into {out_dir}"
    )
    return 1 if failures else 0


def _write_index(project: ProjectConfig, out_dir: Path, records: list[dict]) -> None:
    index = {
        "project": project.title,
        "work": project.work,
        "rights": project.rights,
        "generator": f"parttrack {__version__}",
        "deliverables": records,
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def cmd_rehearse(args: argparse.Namespace) -> int:
    project = ProjectConfig.load(args.project)
    if args.out:
        project.out_dir = Path(args.out)
    if project.recording is None:
        _log(f"{args.project} has no `recording:` block — nothing to cut.")
        return 1

    out_dir = project.output_path / "rehearsal"
    _log(f"project  : {project.title}")
    _log(f"recording: {project.recording.source}")
    _log(f"output   : {out_dir}")

    started = time.time()
    records = build_kit(project, out_dir)
    (out_dir / "index.json").write_text(
        json.dumps(
            {
                "project": project.title,
                "generator": f"parttrack {__version__}",
                "source": str(project.recording.source),
                "items": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    by_kind: dict[str, int] = {}
    for record in records:
        by_kind[record["kind"]] = by_kind.get(record["kind"], 0) + 1
    for kind, count in sorted(by_kind.items()):
        _log(f"  {kind:<14} {count}")
    _log(f"\n{len(records)} files in {time.time() - started:.0f}s -> {out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parttrack",
        description="Batch-generate part-practice audio and piano-roll videos from a score.",
    )
    parser.add_argument("--version", action="version", version=f"parttrack {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check external dependencies")
    doctor.set_defaults(func=cmd_doctor)

    inspect = subparsers.add_parser("inspect", help="summarise a project and its planned outputs")
    inspect.add_argument("project", help="path to the project YAML file")
    inspect.set_defaults(func=cmd_inspect)

    build = subparsers.add_parser("build", help="render every deliverable for a project")
    build.add_argument("project", help="path to the project YAML file")
    build.add_argument("--out", help="override the project's output directory")
    build.add_argument(
        "--only",
        nargs="+",
        metavar="SLUG",
        help="build only these deliverable slugs (or part ids)",
    )
    build.add_argument("--limit", type=int, help="build at most N deliverables")
    build.add_argument("--no-video", action="store_true", help="skip video rendering")
    build.add_argument("--dry-run", action="store_true", help="list deliverables and exit")
    build.add_argument("--fail-fast", action="store_true", help="stop at the first failure")
    build.set_defaults(func=cmd_build)

    rehearse = subparsers.add_parser(
        "rehearse",
        help="cut an actual recording into section loops and slow practice takes",
    )
    rehearse.add_argument("project", help="path to a project YAML with a recording block")
    rehearse.add_argument("--out", help="override the project's output directory")
    rehearse.set_defaults(func=cmd_rehearse)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
