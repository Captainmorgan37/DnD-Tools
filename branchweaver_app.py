# BranchWeaver — Interactive Story & Dialogue Planner
# Streamlit full-featured app (single-file)
# Author: ChatGPT (for Morgan)
#
# Key features
# - Tabs: Overview • Branch Editor • Visualizer • Playback • Generators • World State • Import/Export • Settings
# - Node model: id, title, text, tags, npc, location, emotion, gm_notes, choices[{text, target_id, tags, gate}]
# - Create, edit, duplicate, delete nodes and choices
# - Graphviz visual map with filters and color/shape by type
# - Playback mode to rehearse a conversation/path
# - NPC/Scene snippet generators (rule-based, no external APIs required)
# - JSON import/export + Markdown export (summary or detailed)
# - Auto-save to local JSON during the session

from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, field, asdict, is_dataclass
from typing import Dict, List, Optional

import streamlit as st
import graphviz
import os
from openai import OpenAI


# -------------------------------
# Page & Theme
# -------------------------------
st.set_page_config(
    page_title="BranchWeaver — Story & Dialogue Planner",
    page_icon="🎭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -------------------------------
# Data Model
# -------------------------------
@dataclass
class Choice:
    text: str
    target_id: str
    tags: List[str] = field(default_factory=list)
    gate: str = ""  # e.g., "Persuasion DC 13", "Has the map?", "Morale < 3"


@dataclass
class Node:
    id: str
    title: str
    text: str
    npc: str = ""
    location: str = ""
    emotion: str = ""  # e.g., "wary", "menacing", "jovial"
    tags: List[str] = field(default_factory=list)
    gm_notes: str = ""
    choices: List[Choice] = field(default_factory=list)


@dataclass
class Story:
    title: str = "Untitled Story"
    description: str = ""
    nodes: Dict[str, Node] = field(default_factory=dict)
    start_node_id: Optional[str] = None


# -------------------------------
# Helpers & State
# -------------------------------
AUTOSAVE_PATH = "branchweaver_autosave.json"

if "story" in st.session_state:
    if isinstance(st.session_state.story, str):
        # Old invalid save format — clear it
        st.session_state.story = None


def _new_id() -> str:
    return str(uuid.uuid4())


def ensure_state():
    if "story" not in st.session_state:
        st.session_state.story = Story(
            title="BranchWeaver Project",
            description="A branching story/dialogue for your campaign.",
            nodes={},
            start_node_id=None,
        )
    if "ui" not in st.session_state:
        st.session_state.ui = {
            "selected_node_id": None,
            "filter_text": "",
            "editor_search": "",
            "tag_filter": [],
            "filter_npc": "(Any)",
            "filter_location": "(Any)",
            "filter_show_broken": False,
            "show_gm": True,
            "color_by": "npc",  # npc | location | emotion | none
            "shape_by": "type",  # type | none (start vs normal)
            "playback_node_id": None,
            "playback_history": [],  # list of node_ids visited
            "tone_preset": "Cosmic Absurd",
        }

    # 🔥 Add this NEW block:
    if "has_imported" not in st.session_state:
        st.session_state.has_imported = False

def try_autoload() -> bool:
    """Try to restore story from autosave on disk."""
    if not os.path.exists(AUTOSAVE_PATH):
        return False
    try:
        with open(AUTOSAVE_PATH, "r", encoding="utf-8") as f:
            data = f.read()
        st.session_state.story = story_from_json(data)
        return True
    except Exception:
        return False


def autosave(story: Story) -> None:
    """Persist story to a local json file (works on Cloud for the life of the session)."""
    try:
        with open(AUTOSAVE_PATH, "w", encoding="utf-8") as f:
            f.write(story_to_json(story))
    except Exception:
        # Silent failure is fine; this is just a convenience.
        pass


def node_to_label(n: Node, show_gm: bool = False) -> str:
    """Label for Graphviz nodes."""
    title = n.title or "(untitled)"
    meta = []
    if n.npc:
        meta.append(f"NPC: {n.npc}")
    if n.location:
        meta.append(f"@ {n.location}")
    if n.emotion:
        meta.append(f"[{n.emotion}]")
    meta_str = " ".join(meta)
    gm = f"\nGM: {n.gm_notes}" if (show_gm and n.gm_notes) else ""
    text = (n.text or "").replace("\n", " ")
    if len(text) > 160:
        text = text[:157] + "…"
    label = f"{title}\n{text}\n{meta_str}{gm}"
    return label


COLORS = [
    "#6baed6", "#fd8d3c", "#74c476", "#9e9ac8", "#fdd0a2",
    "#fa9fb5", "#c6dbef", "#fdae6b", "#bcbddc",
    "#9ecae1", "#fcae91", "#c7e9c0", "#dadaeb", "#cbc9e2",
]


def color_for_value(value: str) -> str:
    if not value:
        return "#dddddd"
    idx = abs(hash(value)) % len(COLORS)
    return COLORS[idx]


def add_node(
    story: Story,
    *,
    title: str,
    text: str,
    npc: str = "",
    location: str = "",
    emotion: str = "",
    tags: Optional[List[str]] = None,
    gm_notes: str = "",
) -> str:
    nid = _new_id()
    node = Node(
        id=nid,
        title=title.strip() or "(untitled)",
        text=text.strip(),
        npc=npc.strip(),
        location=location.strip(),
        emotion=emotion.strip(),
        tags=[t.strip() for t in (tags or []) if t.strip()],
        gm_notes=gm_notes.strip(),
        choices=[],
    )
    story.nodes[nid] = node
    if not story.start_node_id:
        story.start_node_id = nid
    return nid


def delete_node(story: Story, node_id: str) -> None:
    if node_id in story.nodes:
        for n in story.nodes.values():
            n.choices = [c for c in n.choices if c.target_id != node_id]
        del story.nodes[node_id]
        if story.start_node_id == node_id:
            story.start_node_id = next(iter(story.nodes.keys()), None)


def duplicate_node(story: Story, node_id: str) -> str:
    n = story.nodes[node_id]
    new_id = _new_id()
    new_node = Node(
        id=new_id,
        title=f"{n.title} (copy)",
        text=n.text,
        npc=n.npc,
        location=n.location,
        emotion=n.emotion,
        tags=list(n.tags),
        gm_notes=n.gm_notes,
        choices=[
            Choice(text=c.text, target_id=c.target_id, tags=list(c.tags), gate=c.gate)
            for c in n.choices
        ],
    )
    story.nodes[new_id] = new_node
    return new_id


# -------------------------------
# Serialization
# -------------------------------
def story_to_json(story: Story) -> str:
    """Safe JSON encoder for Story/Node/Choice."""

    def _encode(obj):
        if is_dataclass(obj):
            return _encode(asdict(obj))
        if isinstance(obj, Story):
            return {
                "title": obj.title,
                "description": obj.description,
                "start_node_id": obj.start_node_id,
                "nodes": {nid: _encode(node) for nid, node in obj.nodes.items()},
            }
        if isinstance(obj, Node):
            return {
                "id": obj.id,
                "title": obj.title,
                "text": obj.text,
                "npc": obj.npc,
                "location": obj.location,
                "emotion": obj.emotion,
                "tags": obj.tags,
                "gm_notes": obj.gm_notes,
                "choices": [_encode(c) for c in obj.choices],
            }
        if isinstance(obj, Choice):
            return {
                "text": obj.text,
                "target_id": obj.target_id,
                "tags": obj.tags,
                "gate": obj.gate,
            }
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        if isinstance(obj, list):
            return [_encode(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _encode(v) for k, v in obj.items()}
        # Fallback: stringify anything weird instead of crashing
        return str(obj)

    # ensure_ascii=False preserves any non-ASCII characters and the indent
    # keeps the file human-readable (and proper JSON instead of a quoted blob)
    return json.dumps(_encode(story), indent=2, ensure_ascii=False)


def story_to_json_bytes(story: Story) -> bytes:
    """Convenience helper to provide UTF-8 encoded JSON for downloads."""

    return story_to_json(story).encode("utf-8")


def story_from_json(s: str) -> Story:
    data = json.loads(s)
    nodes: Dict[str, Node] = {}
    for nid, nd in data.get("nodes", {}).items():
        choices_raw = nd.get("choices", [])
        choices: List[Choice] = []
        for c in choices_raw:
            if not isinstance(c, dict):
                continue
            choices.append(
                Choice(
                    text=c.get("text", ""),
                    target_id=c.get("target_id", ""),
                    tags=c.get("tags", []),
                    gate=c.get("gate", ""),
                )
            )
        nodes[nid] = Node(
            id=nd.get("id", nid),
            title=nd.get("title", "(untitled)"),
            text=nd.get("text", ""),
            npc=nd.get("npc", ""),
            location=nd.get("location", ""),
            emotion=nd.get("emotion", ""),
            tags=nd.get("tags", []),
            gm_notes=nd.get("gm_notes", ""),
            choices=choices,
        )
    return Story(
        title=data.get("title", "Untitled Story"),
        description=data.get("description", ""),
        nodes=nodes,
        start_node_id=data.get("start_node_id"),
    )


def export_markdown(story: Story, detailed: bool = False) -> str:
    lines = [f"# {story.title}", ""]
    if story.description:
        lines.append(story.description)
        lines.append("")

    order = list(story.nodes.keys())
    if story.start_node_id in order:
        order.remove(story.start_node_id)
        order.insert(0, story.start_node_id)

    for nid in order:
        n = story.nodes[nid]
        lines.append(f"## {n.title} ({nid[:8]})")
        if n.npc or n.location or n.emotion:
            meta = [x for x in [n.npc, n.location, n.emotion] if x]
            lines.append("*" + " • ".join(meta) + "*")
        if n.tags:
            lines.append("Tags: " + ", ".join(n.tags))
        lines.append("")
        lines.append(n.text)
        if detailed and n.gm_notes:
            lines.append("")
            lines.append(f"> **GM Notes:** {n.gm_notes}")
        if n.choices:
            lines.append("")
            lines.append("**Choices**")
            for c in n.choices:
                gate = f" [{c.gate}]" if c.gate else ""
                tag = f" (tags: {', '.join(c.tags)})" if c.tags else ""
                lines.append(f"- {c.text}{gate} → `{c.target_id[:8]}`{tag}")
        lines.append("")
    return "\n".join(lines)

# -------------------------------
# OpenAI helpers (AI integration)
# -------------------------------

@st.cache_resource
def get_openai_client() -> Optional[OpenAI]:
    """
    Returns a cached OpenAI client if an API key is available in Streamlit secrets.
    Expects st.secrets["openai"]["api_key"] to be set.
    """
    api_key = None
    try:
        api_key = st.secrets["openai"]["api_key"]
    except Exception:
        pass

    if not api_key:
        return None

    return OpenAI(api_key=api_key)


def build_story_context(story: Story, max_nodes: int = 40) -> str:
    """
    Build a compact textual summary of the current story to give the model
    full-context awareness (NPCs, locations, tags, GM notes, key beats).
    """
    lines = []
    lines.append(f"Story title: {story.title}")
    if story.description:
        lines.append(f"Story description: {story.description}")

    # Collect NPCs, locations, tags
    npcs = sorted({n.npc for n in story.nodes.values() if n.npc})
    locs = sorted({n.location for n in story.nodes.values() if n.location})
    tags = sorted({t for n in story.nodes.values() for t in n.tags})

    if npcs:
        lines.append("NPCs: " + ", ".join(npcs))
    if locs:
        lines.append("Locations: " + ", ".join(locs))
    if tags:
        lines.append("Tags/themes: " + ", ".join(tags))

    # Summarize up to max_nodes
    lines.append("")
    lines.append(f"=== Node summaries (up to {max_nodes}) ===")
    node_items = list(story.nodes.items())

    # Try to put start node first
    if story.start_node_id and story.start_node_id in story.nodes:
        node_items.sort(key=lambda kv: kv[0] != story.start_node_id)

    for i, (nid, n) in enumerate(node_items[:max_nodes]):
        snippet = (n.text or "").replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:157] + "…"
        lines.append(
            f"- Node {i+1}: id={nid[:8]}, title='{n.title}', "
            f"npc='{n.npc}', location='{n.location}', emotion='{n.emotion}', "
            f"tags={n.tags}, gm_notes='{(n.gm_notes or '')[:80]}', text_snippet='{snippet}'"
        )

    return "\n".join(lines)


def apply_ai_nodes_to_story(story: Story, ai_json: str, attach_parent_id: Optional[str] = None, attach_choice_text: Optional[str] = None) -> List[str]:
    """
    Takes AI-generated JSON and inserts nodes into the story.

    Expected JSON format:
    {
      "nodes": [
        {
          "title": "...",
          "text": "...",
          "npc": "...",
          "location": "...",
          "emotion": "...",
          "tags": ["..."],
          "gm_notes": "...",
          "choices": [
            {
              "text": "...",
              "gate": "...",
              "tags": ["..."],
              "target_title": "Title of another node in this list (optional)"
            }
          ]
        },
        ...
      ]
    }

    Returns list of new node IDs.
    """
    data = json.loads(ai_json)
    nodes_def = data.get("nodes", [])
    if not isinstance(nodes_def, list) or not nodes_def:
        raise ValueError("AI JSON must contain a non-empty 'nodes' list")

    title_to_id: Dict[str, str] = {}
    # First pass: create nodes
    for nd in nodes_def:
        title = nd.get("title", "Untitled").strip() or "Untitled"
        text = nd.get("text", "")
        npc = nd.get("npc", "")
        location = nd.get("location", "")
        emotion = nd.get("emotion", "")
        tags = nd.get("tags", []) or []
        gm_notes = nd.get("gm_notes", "")

        nid = add_node(
            story,
            title=title,
            text=text,
            npc=npc,
            location=location,
            emotion=emotion,
            tags=tags,
            gm_notes=gm_notes,
        )
        title_to_id[title] = nid

    # Second pass: wire internal choices
    for nd in nodes_def:
        title = nd.get("title", "").strip()
        nid = title_to_id.get(title)
        if not nid:
            continue
        node = story.nodes[nid]
        for ch_def in nd.get("choices", []) or []:
            c_text = ch_def.get("text", "")
            gate = ch_def.get("gate", "")
            c_tags = ch_def.get("tags", []) or []
            tgt_title = (ch_def.get("target_title") or "").strip()
            target_id = title_to_id.get(tgt_title, "")
            node.choices.append(
                Choice(
                    text=c_text,
                    target_id=target_id,
                    tags=c_tags,
                    gate=gate,
                )
            )

    new_ids = list(title_to_id.values())

    # Optionally attach the first new node to an existing parent as a choice
    if attach_parent_id and attach_choice_text and new_ids:
        parent = story.nodes.get(attach_parent_id)
        if parent:
            parent.choices.append(
                Choice(
                    text=attach_choice_text,
                    target_id=new_ids[0],
                    tags=[],
                    gate="",
                )
            )

    return new_ids


# -------------------------------
# Seed Content (optional)
# -------------------------------
SEED_STORY = {
    "title": "Cragmaw: King Grol's Gambit",
    "description": "A branching confrontation with King Grol; humor curls around cosmic dread.",
    "nodes": [
        {
            "title": "Throne of King Grol",
            "text": "The hall opens wide. Grol leans forward, crown of twisted iron. 'You come uninvited.'",
            "npc": "King Grol",
            "location": "Cragmaw Castle — Chamber 5",
            "emotion": "menacing",
            "tags": ["intro", "grol"],
            "gm_notes": "He wants tribute or to intimidate them. Hidden Devourer influence.",
            "choices": [
                {"text": "Offer gold tribute", "target": "Tribute Accepted"},
                {"text": "Threaten him", "target": "Unholy Strength Stirs", "gate": "Intimidation DC15"},
                {"text": "Parley about the map", "target": "Trade for the Map"},
            ],
        },
        {
            "title": "Tribute Accepted",
            "text": "Grol grins too wide. The court hushes. Something in the rafters clicks. He asks for more.",
            "npc": "King Grol",
            "location": "Chamber 5",
            "emotion": "greedy",
            "tags": ["negotiation"],
            "gm_notes": "He will betray any deal.",
            "choices": [
                {"text": "Appeal to pride", "target": "A Toast to Kings"},
                {"text": "Reveal a secret", "target": "Whispers in the Dark", "gate": "Deception DC14"},
            ],
        },
        {
            "title": "Unholy Strength Stirs",
            "text": "Grol's flesh splits; eyes bloom like ulcers. The crowd gasps. Shadows thicken.",
            "npc": "King Grol",
            "location": "Chamber 5",
            "emotion": "wrathful",
            "tags": ["phase2", "combat"],
            "gm_notes": "Phase 2 boosts; psychic bleed.",
        },
        {
            "title": "Trade for the Map",
            "text": "He considers a trade. The map sweats ink. 'What do you offer, soft things?'",
            "npc": "King Grol",
            "location": "Chamber 5",
            "emotion": "calculating",
            "tags": ["map", "deal"],
            "gm_notes": "He wants leverage on the Devourer cult.",
            "choices": [
                {"text": "Promise to slay a rival", "target": "A Rival Named"},
                {"text": "Offer a cursed relic", "target": "The Relic Hungers", "gate": "Arcana DC13"},
            ],
        },
    ],
}


def load_seed(story: Story, seed=SEED_STORY) -> None:
    """Populate story from a simple seed structure."""
    title = seed.get("title", "Seed Story")
    description = seed.get("description", "")
    id_map: Dict[str, str] = {}

    # Create nodes first
    for entry in seed.get("nodes", []):
        nid = add_node(
            story,
            title=entry.get("title", "Untitled"),
            text=entry.get("text", ""),
            npc=entry.get("npc", ""),
            location=entry.get("location", ""),
            emotion=entry.get("emotion", ""),
            tags=entry.get("tags", []),
            gm_notes=entry.get("gm_notes", ""),
        )
        id_map[entry.get("title", nid)] = nid

    # Wire choices
    for entry in seed.get("nodes", []):
        src_title = entry.get("title", "")
        src_id = id_map.get(src_title)
        if not src_id:
            continue
        for ch in entry.get("choices", []):
            tgt_title = ch.get("target", "")
            tgt_id = id_map.get(tgt_title)
            if not tgt_id:
                continue
            story.nodes[src_id].choices.append(
                Choice(
                    text=ch.get("text", ""),
                    target_id=tgt_id,
                    tags=list(ch.get("tags", [])),
                    gate=ch.get("gate", ""),
                )
            )

    story.title = title
    story.description = description


# -------------------------------
# UI Components
# -------------------------------
def sidebar_project(story: Story):
    st.sidebar.subheader("🗂️ Project")
    story.title = st.sidebar.text_input("Story Title", story.title)
    story.description = st.sidebar.text_area("Description", story.description, height=80)

    if st.sidebar.button("🌱 Load Seed (Grol)"):
        st.session_state.story = Story()
        load_seed(st.session_state.story)
        story = st.session_state.story
        first_id = story.start_node_id or next(iter(story.nodes.keys()), None)
        st.session_state.ui["selected_node_id"] = first_id
        st.session_state.ui["playback_node_id"] = first_id
        st.session_state.ui["playback_history"] = [first_id] if first_id else []
        st.sidebar.success("Seed loaded.")
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("🔎 Graph Filters")
    st.session_state.ui["filter_text"] = st.sidebar.text_input(
        "Search (title/text)", st.session_state.ui["filter_text"]
    )
    st.session_state.ui["show_gm"] = st.sidebar.checkbox(
        "Show GM notes on nodes", st.session_state.ui["show_gm"]
    )
    st.session_state.ui["color_by"] = st.sidebar.selectbox(
        "Color by",
        ["npc", "location", "emotion", "none"],
        index=["npc", "location", "emotion", "none"].index(
            st.session_state.ui["color_by"]
        ),
    )
    st.session_state.ui["shape_by"] = st.sidebar.selectbox(
        "Shape by",
        ["type", "none"],
        index=["type", "none"].index(st.session_state.ui["shape_by"]),
    )


# ------------- Tab: Overview -------------
def tab_overview(story: Story):
    c1, c2 = st.columns([2, 3])
    with c1:
        st.markdown(f"### 📘 {story.title}")
        st.write(story.description or "No description yet.")
        start_label = story.start_node_id[:8] if story.start_node_id else "n/a"
        st.caption(f"Nodes: {len(story.nodes)} | Start: {start_label}")
        if st.button("➕ Quick Add 'Beat' Node"):
            nid = add_node(story, title="New Beat", text="Describe the beat…")
            st.session_state.ui["selected_node_id"] = nid
            st.rerun()

    with c2:
        st.markdown("### 🧾 Recently Edited")
        recent = list(story.nodes.values())[-5:]
        if not recent:
            st.info("No nodes yet. Add one in the Branch Editor tab.")
        for n in reversed(recent):
            with st.expander(f"{n.title}  ·  {n.id[:8]}"):
                st.write(n.text)
                meta = []
                if n.npc:
                    meta.append(f"NPC: {n.npc}")
                if n.location:
                    meta.append(f"@ {n.location}")
                if n.emotion:
                    meta.append(f"[{n.emotion}]")
                if meta:
                    st.caption(" • ".join(meta))
                if n.gm_notes:
                    st.caption("GM: " + n.gm_notes)
                if st.button("Edit This Node", key=f"ov_edit_{n.id}"):
                    st.session_state.ui["selected_node_id"] = n.id
                    st.rerun()


# ------------- Tab: Branch Editor -------------
def tab_editor(story: Story):
    left, right = st.columns([2, 3])

    # -------- LEFT: Node list & actions --------
    with left:
        st.subheader("🧩 Nodes")
        st.caption("Filter by text, tags, NPC, location, or show only nodes with broken links.")

        search_val = st.text_input(
            "Search (title, text, NPC, notes)",
            value=st.session_state.ui.get("editor_search", ""),
            placeholder="e.g., Toblen, haunting, 'Gate DC'",
        )
        st.session_state.ui["editor_search"] = search_val

        available_tags = sorted({t for n in story.nodes.values() for t in n.tags})
        preserved_tags = [
            t for t in st.session_state.ui.get("tag_filter", []) if t in available_tags
        ]
        tag_filter = st.multiselect(
            "Tags",
            options=available_tags,
            default=preserved_tags,
            help="Show nodes that include all selected tags.",
        )
        st.session_state.ui["tag_filter"] = tag_filter

        npc_options = ["(Any)"] + sorted({n.npc for n in story.nodes.values() if n.npc})
        npc_sel = st.selectbox(
            "NPC",
            npc_options,
            index=npc_options.index(st.session_state.ui.get("filter_npc", "(Any)"))
            if st.session_state.ui.get("filter_npc", "(Any)") in npc_options
            else 0,
        )
        st.session_state.ui["filter_npc"] = npc_sel

        loc_options = ["(Any)"] + sorted(
            {n.location for n in story.nodes.values() if n.location}
        )
        loc_sel = st.selectbox(
            "Location",
            loc_options,
            index=loc_options.index(st.session_state.ui.get("filter_location", "(Any)"))
            if st.session_state.ui.get("filter_location", "(Any)") in loc_options
            else 0,
        )
        st.session_state.ui["filter_location"] = loc_sel

        show_broken_only = st.checkbox(
            "Only show nodes with unwired or missing-choice targets",
            value=st.session_state.ui.get("filter_show_broken", False),
        )
        st.session_state.ui["filter_show_broken"] = show_broken_only

        q = search_val.lower().strip()
        tag_filter_set = set(tag_filter)

        filtered_items = []
        for nid, node in story.nodes.items():
            matches = True
            if q and not (
                q in node.title.lower()
                or q in (node.text or "").lower()
                or q in (node.npc or "").lower()
                or q in (node.gm_notes or "").lower()
            ):
                matches = False
            if matches and tag_filter_set and not tag_filter_set.issubset(set(node.tags)):
                matches = False
            if matches and npc_sel != "(Any)" and node.npc != npc_sel:
                matches = False
            if matches and loc_sel != "(Any)" and node.location != loc_sel:
                matches = False

            broken = any(
                (not ch.target_id) or (ch.target_id not in story.nodes)
                for ch in node.choices
            )
            if matches and show_broken_only and not broken:
                matches = False

            if matches:
                filtered_items.append((nid, node, broken))

        filtered_items.sort(key=lambda kv: kv[1].title.lower())

        selected_id = st.session_state.ui.get("selected_node_id")

        if not filtered_items:
            st.info("No nodes yet. Create one to get started.")
            if st.button("➕ New First Node", use_container_width=True):
                nid = add_node(story, title="New Node", text="")
                st.session_state.ui["selected_node_id"] = nid
                st.rerun()
        else:
            options = [
                f"{'⚠️ ' if broken else ''}{v.title}  ·  {k[:8]}"
                for k, v, broken in filtered_items
            ]
            ids = [k for k, _, _ in filtered_items]

            if selected_id not in ids:
                selected_id = ids[0]
                st.session_state.ui["selected_node_id"] = selected_id

            idx = ids.index(selected_id)
            sel = st.selectbox("Select a node", options, index=idx)
            selected_id = ids[options.index(sel)]
            st.session_state.ui["selected_node_id"] = selected_id

            sel_node = story.nodes.get(selected_id)
            if sel_node:
                meta_bits = [
                    part
                    for part in [sel_node.npc or None, sel_node.location or None, sel_node.emotion or None]
                    if part
                ]
                meta_line = " • ".join(meta_bits) if meta_bits else "No metadata set."
                st.caption(meta_line)

                broken = any(
                    (not ch.target_id) or (ch.target_id not in story.nodes)
                    for ch in sel_node.choices
                )
                if broken:
                    st.warning(
                        "This node has choices that are not wired to a valid target yet."
                    )

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("➕ New", use_container_width=True):
                nid = add_node(story, title="New Node", text="")
                st.session_state.ui["selected_node_id"] = nid
                st.rerun()
        with c2:
            if selected_id and st.button("📄 Duplicate", use_container_width=True):
                new_id = duplicate_node(story, selected_id)
                st.session_state.ui["selected_node_id"] = new_id
                st.rerun()
        with c3:
            if selected_id and st.button("⭐ Make Start", use_container_width=True):
                story.start_node_id = selected_id
        with c4:
            if selected_id and st.button("🗑️ Delete", use_container_width=True):
                delete_node(story, selected_id)
                st.session_state.ui["selected_node_id"] = None
                st.rerun()

    # -------- RIGHT: Node editor --------
    with right:
        st.subheader("✍️ Node Editor")
        selected_id = st.session_state.ui.get("selected_node_id")
        if not selected_id or selected_id not in story.nodes:
            st.info("Select or create a node to edit.")
            return

        node = story.nodes[selected_id]

        # --- FORM: node fields only ---
        title_key = f"title_input_{selected_id}"
        text_key = f"text_input_{selected_id}"
        gm_key = f"gm_input_{selected_id}"
        npc_key = f"npc_input_{selected_id}"
        loc_key = f"loc_input_{selected_id}"
        emo_key = f"emo_input_{selected_id}"
        tag_key = f"tag_input_{selected_id}"

        with st.form(key=f"edit_{selected_id}_form"):
            cA, cB = st.columns([2, 1])
            with cA:
                title_val = st.text_input(
                    "Title / Speaker",
                    value=node.title,
                    key=title_key,
                )
                text_val = st.text_area(
                    "Scene / Dialogue Text",
                    value=node.text,
                    height=160,
                    key=text_key,
                )
                gm_val = st.text_area(
                    "GM Notes (hidden)",
                    value=node.gm_notes,
                    height=80,
                    key=gm_key,
                )
            with cB:
                npc_val = st.text_input("NPC", value=node.npc, key=npc_key)
                loc_val = st.text_input(
                    "Location", value=node.location, key=loc_key
                )
                emo_val = st.text_input("Emotion", value=node.emotion, key=emo_key)
                tag_str = st.text_input(
                    "Tags (comma-separated)",
                    value=", ".join(node.tags),
                    key=tag_key,
                )

            save_clicked = st.form_submit_button("💾 Save Node Details")
            reset_clicked = st.form_submit_button("↩️ Reset form", type="secondary")

        if reset_clicked:
            st.session_state[title_key] = node.title
            st.session_state[text_key] = node.text
            st.session_state[gm_key] = node.gm_notes
            st.session_state[npc_key] = node.npc
            st.session_state[loc_key] = node.location
            st.session_state[emo_key] = node.emotion
            st.session_state[tag_key] = ", ".join(node.tags)
            st.rerun()

        if save_clicked:
            node.title = title_val.strip() or "(untitled)"
            node.text = text_val.strip()
            node.gm_notes = gm_val.strip()
            node.npc = npc_val.strip()
            node.location = loc_val.strip()
            node.emotion = emo_val.strip()
            node.tags = [t.strip() for t in tag_str.split(",") if t.strip()]
            st.success("Node details saved.")

        st.markdown("---")
        st.markdown("#### Choices / Branches")

        # Prepare list of nodes for target selection
        node_ids = list(story.nodes.keys())
        node_labels = [
            f"{story.nodes[nid].title} · {nid[:8]}" for nid in node_ids
        ]
        target_choices = [
            ("", "🚧 Unlinked — decide later")
        ] + list(zip(node_ids, node_labels))

        # --- Existing choices ---
        for i, ch in enumerate(list(node.choices)):
            with st.expander(f"Choice {i+1}: {ch.text or '(untitled)'}"):
                ch.text = st.text_input(
                    "Choice text", value=ch.text, key=f"ct_{selected_id}_{i}"
                )
                ch.gate = st.text_input(
                    "Gate/Requirement (optional)",
                    value=ch.gate,
                    key=f"gate_{selected_id}_{i}",
                )
                ch.tags = [
                    t.strip()
                    for t in st.text_input(
                        "Tags (comma-separated)",
                        value=", ".join(ch.tags),
                        key=f"ctags_{selected_id}_{i}",
                    ).split(",")
                    if t.strip()
                ]

                # Target selector with placeholder
                target_idx = 0
                for idx, (nid, _) in enumerate(target_choices):
                    if nid == ch.target_id:
                        target_idx = idx
                        break
                sel_target = st.selectbox(
                    "Leads to node",
                    target_choices,
                    index=target_idx,
                    format_func=lambda item: item[1],
                    key=f"sel_{selected_id}_{i}",
                )
                ch.target_id = sel_target[0]

                if not ch.target_id:
                    st.info("This choice is saved but not wired to a target yet.")
                elif ch.target_id not in story.nodes:
                    st.error(
                        "Target node is missing. Choose a new destination or delete this choice."
                    )
                else:
                    target_node = story.nodes[ch.target_id]
                    st.caption(
                        f"Goes to **{target_node.title}** ({target_node.id[:8]})."
                    )

                col_rm, col_up, col_dn = st.columns(3)
                if col_rm.button("Remove", key=f"rm_{selected_id}_{i}"):
                    node.choices.pop(i)
                    st.rerun()
                if col_up.button("↑ Move", key=f"up_{selected_id}_{i}") and i > 0:
                    node.choices[i - 1], node.choices[i] = (
                        node.choices[i],
                        node.choices[i - 1],
                    )
                    st.rerun()
                if (
                    col_dn.button("↓ Move", key=f"dn_{selected_id}_{i}")
                    and i < len(node.choices) - 1
                ):
                    node.choices[i + 1], node.choices[i] = (
                        node.choices[i],
                        node.choices[i + 1],
                    )
                    st.rerun()

        # --- Add new choice ---
        st.markdown("**Add Choice**")
        new_c_text = st.text_input("New choice text", key=f"newct_{selected_id}")
        if node_ids:
            default_idx = next(
                (idx for idx, (nid, _) in enumerate(target_choices) if nid == selected_id),
                0,
            )
        else:
            default_idx = 0
        tar_sel = st.selectbox(
            "Target node",
            target_choices if target_choices else [("", "(no nodes)")],
            index=min(default_idx, len(target_choices) - 1) if target_choices else 0,
            format_func=lambda item: item[1],
            key=f"newtar_{selected_id}",
        )
        req = st.text_input("Gate (opt.)", key=f"newgate_{selected_id}")

        if st.button("➕ Add Choice", key=f"addchoice_{selected_id}") and new_c_text:
            target_id = tar_sel[0]
            node.choices.append(
                Choice(text=new_c_text, target_id=target_id, gate=req)
            )
            st.rerun()


# ------------- Tab: Visualizer -------------

def tab_visualizer(story: Story):
    st.subheader("🕸️ Branch Map")

    q = st.session_state.ui["filter_text"].lower().strip()
    show_gm = st.session_state.ui["show_gm"]
    color_by = st.session_state.ui["color_by"]
    shape_by = st.session_state.ui["shape_by"]

    legend_entries = {}

    # Build DOT graph (no call to dot.pipe / no system graphviz needed)
    dot = graphviz.Digraph("branchweaver")
    dot.attr(rankdir="LR")

    # Nodes
    for nid, n in story.nodes.items():
        if q and (q not in n.title.lower() and q not in n.text.lower()):
            continue

        # color
        color_val = None
        if color_by == "npc":
            color_val = n.npc
        elif color_by == "location":
            color_val = n.location
        elif color_by == "emotion":
            color_val = n.emotion
        fill = color_for_value(color_val) if color_by != "none" else "#ffffff"
        style = "filled" if color_by != "none" else "solid"

        if color_by != "none":
            legend_label = color_val or "(Unspecified)"
            if legend_label not in legend_entries:
                legend_entries[legend_label] = fill

        # shape
        shape = "oval"
        if shape_by == "type":
            shape = "doublecircle" if story.start_node_id == nid else "box"

        label = node_to_label(n, show_gm)
        dot.node(nid, label=label, shape=shape, style=style, fillcolor=fill)

    # Edges
    for nid, n in story.nodes.items():
        if q and (q not in n.title.lower() and q not in n.text.lower()):
            continue
        for ch in n.choices:
            if ch.target_id not in story.nodes:
                continue
            gate = f" [{ch.gate}]" if ch.gate else ""
            edge_label = (ch.text or "") + gate
            dot.edge(nid, ch.target_id, label=edge_label)

    dot_source = dot.source
    dot_js = json.dumps(dot_source)  # safe escape as JS string

    def make_viz_html(container_id: str, height_css: str) -> str:
        # Minimal embedded Panzoom (Panzoom v9.4.0 UMD minified)
        panzoom_js = r"""
        /*! Panzoom v9.4.0 UMD prebundle */
        (function(global,factory){typeof exports==="object"&&typeof module!=="undefined"?module.exports=factory():typeof define==="function"&&define.amd?define(factory):(global=typeof globalThis!=="undefined"?globalThis:global||self,global.Panzoom=factory());})(this,(function(){function e(e,t){return Math.abs(e-t)<1e-7}return function(t,n){n=n||{};var o=t,a=o.parentElement,r=n.startX||0,i=n.startY||0,c=n.scale||1,l=!1,s=null,u=null,d=o.style,f=n.maxScale||5,g=n.minScale||.1;function m(e){return e.preventDefault(),!1}function p(e){if(!l)return;var t=e.clientX,i=e.clientY;s=r+t-u,u=i,l&&h(0,0,1)}function v(e){l=!1,document.removeEventListener("mousemove",p),document.removeEventListener("mouseup",v)}function h(e,t,n){var a=c*n;a>f&&(a=f),a<g&&(a=g);var l=a/c;r=r* l+ e*(1-l),i=i* l+ t*(1-l),c=a,d.transform="translate("+r+"px,"+i+"px) scale("+c+")"}o.addEventListener("mousedown",(function(e){l=!0,u=e.clientY,s=e.clientX-r,document.addEventListener("mousemove",p),document.addEventListener("mouseup",v)})),o.addEventListener("wheel",(function(t){t.preventDefault();var n=t.deltaY<0?1.1:.9,e=t.offsetX,o=t.offsetY;h(e-r,o-i,n)}),{passive:!1}),d.transformOrigin="0 0",d.willChange="transform";return{zoom:function(e){h(0,0,e)},zoomWithWheel:function(e){var t=e.deltaY<0?1.1:.9;h(e.offsetX-r,e.offsetY-i,t)},getScale:function(){return c}}};}));
        """
    
        return f"""
        <div id="{container_id}" style="width: 100%; height: {height_css}; border: 1px solid #444; background-color: white; overflow: hidden;">
          <div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;">
            <span style="color: #666; font-family: sans-serif;">Rendering graph…</span>
          </div>
        </div>
    
        <!-- Viz.js -->
        <script src="https://cdnjs.cloudflare.com/ajax/libs/viz.js/2.1.2/viz.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/viz.js/2.1.2/full.render.js"></script>
    
        <!-- Embedded Panzoom -->
        <script>{panzoom_js}</script>
    
        <script>
        const dot = {dot_js};
    
        (function() {{
            const container = document.getElementById("{container_id}");
            if (!container) return;
    
            const viz = new Viz();
    
            viz.renderSVGElement(dot)
              .then(function(svg) {{
                container.innerHTML = "";
                svg.style.width = "100%";
                svg.style.height = "100%";
                svg.style.display = "block";
                container.appendChild(svg);
    
                const panzoom = Panzoom(svg, {{
                    maxScale: 5,
                    minScale: 0.2
                }});
    
                container.addEventListener('wheel', function(event) {{
                    event.preventDefault();
                    panzoom.zoomWithWheel(event);
                }});
    
                panzoom.zoom(0.8);
              }})
              .catch(function(error) {{
                console.error(error);
                container.innerHTML = "<pre style='color:red; white-space: pre-wrap;'>" + error + "</pre>";
              }});
        }})();
        </script>
        """


    # --- Inline panel view ---
    st.markdown("#### Inline View")
    if color_by != "none" and legend_entries:
        st.markdown("**Color legend**")
        legend_cols = st.columns(min(4, len(legend_entries)))
        for idx, (label, color) in enumerate(sorted(legend_entries.items())):
            col = legend_cols[idx % len(legend_cols)]
            col.markdown(
                f"<div style='display:flex;align-items:center;gap:0.5rem;'>"
                f"<span style='width:16px;height:16px;border-radius:4px;display:inline-block;background:{color};border:1px solid #999;'></span>"
                f"<span style='font-size:0.85rem'>{label}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        st.caption(
            "Colors follow the 'Color by' selection above. Nodes with no value use a muted gray."
        )
    if shape_by == "type":
        st.caption("Shape legend: double circle = start node, box = standard node.")
    inline_html = make_viz_html("branchweaver_inline", "70vh")
    st.components.v1.html(inline_html, height=650, scrolling=False)

    # --- Optional 'fullscreen' style view ---
    show_full = st.checkbox("Show full-screen map", key="show_full_viz")
    if show_full:
        st.markdown("#### Full-Screen Map")
        full_html = make_viz_html("branchweaver_full", "90vh")
        st.components.v1.html(full_html, height=900, scrolling=False)

    # DOT download as before
    st.download_button(
        label="⬇️ Download DOT",
        data=dot_source,
        file_name="branchweaver_graph.dot",
        mime="text/plain",
    )




# ------------- Tab: Playback -------------
def tab_playback(story: Story):
    st.subheader("🎬 Playback — Rehearse a Path")

    if not story.nodes:
        st.info("No nodes in the story yet. Add some in the Branch Editor.")
        return

    # --- Choose starting node safely ---
    ids = list(story.nodes.keys())
    labels = [f"{story.nodes[i].title} · {i[:8]}" for i in ids]

    if story.start_node_id in ids:
        start_idx = ids.index(story.start_node_id)
    else:
        story.start_node_id = ids[0]
        start_idx = 0

    start_label = st.selectbox("Start at", labels, index=start_idx)
    start_id = ids[labels.index(start_label)]

    colx, coly = st.columns(2)
    with colx:
        if st.button("🔁 Restart"):
            st.session_state.ui["playback_node_id"] = start_id
            st.session_state.ui["playback_history"] = [start_id]
            st.rerun()
    with coly:
        if st.button("⬅️ Step Back"):
            hist = st.session_state.ui.get("playback_history", [])
            if len(hist) > 1:
                hist.pop()
                st.session_state.ui["playback_history"] = hist
                st.session_state.ui["playback_node_id"] = hist[-1]
                st.rerun()

    # --- Initialize / validate current node ---
    curr = st.session_state.ui.get("playback_node_id")
    if not curr or curr not in story.nodes:
        curr = start_id
        st.session_state.ui["playback_node_id"] = curr
        st.session_state.ui["playback_history"] = [curr]

    current_id = st.session_state.ui["playback_node_id"]
    if current_id not in story.nodes:
        st.warning("Current node missing.")
        return

    n = story.nodes[current_id]

    # ===============================
    # 🧠 Live DM Assist (text-only)
    # ===============================
    with st.expander("🧠 Live DM Assist (AI — suggest NPC reply & branches)", expanded=False):
        client = get_openai_client()
        if client is None:
            st.info(
                "Add an OpenAI API key in `st.secrets['openai']['api_key']` to enable the DM Assist, "
                "or use the 🧠 AI Assistant tab for offline tools."
            )
        else:
            st.caption(
                "Describe what the **players just said**. The AI will respond as the current NPC, "
                "using this scene, GM notes, and your story history."
            )
            player_input = st.text_area(
                "What did the players say?",
                key=f"dmassist_player_input_{current_id}",
                height=80,
                placeholder="e.g., 'We tell Toblen we slept like babies and ask again about the \"haunting\".'",
            )

            if st.button("🎭 Generate NPC response", key=f"dmassist_btn_{current_id}"):
                # Build playback history titles
                playback_hist = st.session_state.ui.get("playback_history", [])
                hist_titles = [
                    story.nodes[nid].title
                    for nid in playback_hist
                    if nid in story.nodes
                ]

                # Full story context for tone/continuity
                ctx = build_story_context(story)

                system_msg = (
                    "You are BranchWeaver DM Assist, a helper for a D&D DM.\n"
                    "Your job is to generate in-character NPC responses and DM guidance.\n\n"
                    "Tone: 'Lovecraft through a Monty Python lens' — cosmic dread with streaks of absurd humor.\n"
                    "Always keep it usable at the table: short-ish NPC speech, clear cues for the DM, and "
                    "optional ideas for branching outcomes.\n"
                    "Respond in plain Markdown with these sections:\n\n"
                    "### NPC Response\n"
                    "- What the NPC actually says, as dialogue.\n\n"
                    "### Scene Shift\n"
                    "- How the environment / other NPCs react.\n\n"
                    "### DM Options\n"
                    "- 2–4 bullet points of ways the scene could branch.\n"
                )

                user_msg = (
                    "=== CURRENT NODE ===\n"
                    f"Title: {n.title}\n"
                    f"NPC: {n.npc}\n"
                    f"Location: {n.location}\n"
                    f"Emotion: {n.emotion}\n"
                    f"Tags: {n.tags}\n"
                    f"GM Notes: {n.gm_notes}\n"
                    f"Text:\n{n.text}\n\n"
                    "=== PLAYBACK HISTORY (titles in order) ===\n"
                    + " -> ".join(hist_titles) + "\n\n"
                    "=== PLAYER INPUT ===\n"
                    f"{player_input}\n\n"
                    "=== BROADER STORY CONTEXT ===\n"
                    f"{ctx}\n\n"
                    "Now, staying consistent with all of this, generate the sections requested."
                )

                try:
                    resp = client.chat.completions.create(
                        model="gpt-4.1-mini",
                        messages=[
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                        ],
                    )
                    reply = resp.choices[0].message.content.strip()

                    # Store per-node so navigating doesn't mix responses
                    st.session_state["dmassist_last_reply"] = reply
                    st.session_state["dmassist_last_node_id"] = current_id
                    st.success("NPC response generated.")
                except Exception as e:
                    st.error(f"AI generation failed: {e}")

            # Show last reply, if for this node
            last_reply = st.session_state.get("dmassist_last_reply")
            last_node = st.session_state.get("dmassist_last_node_id")
            if last_reply and last_node == current_id:
                st.markdown("#### 📜 Suggested Response & Branch Ideas")
                st.markdown(last_reply)

                st.markdown("---")
                st.markdown("#### ➕ Turn this into a new node")
                default_choice = "Follow this conversation"
                choice_text = st.text_input(
                    "Choice text on this node to reach the new one:",
                    value=default_choice,
                    key=f"dmassist_choice_text_{current_id}",
                )
                if st.button(
                    "➕ Create node from reply & add choice",
                    key=f"dmassist_make_node_{current_id}",
                ):
                    new_id = add_node(
                        story,
                        title=f"{n.npc or 'NPC'} reply",
                        text=last_reply,
                        npc=n.npc,
                        location=n.location,
                        emotion=n.emotion,
                        tags=list(n.tags),
                        gm_notes="Generated via Live DM Assist from table dialogue.",
                    )
                    # Wire choice from current node to new node
                    n.choices.append(
                        Choice(
                            text=choice_text or default_choice,
                            target_id=new_id,
                            tags=["ai_generated"],
                            gate="",
                        )
                    )
                    st.session_state.ui["selected_node_id"] = new_id
                    st.success(f"Created new node ({new_id[:8]}) and linked it as a choice.")
                    st.rerun()

    # ===============================
    # Normal playback display
    # ===============================
    st.markdown("---")
    st.markdown(f"### {n.title}")
    if n.npc or n.location or n.emotion:
        meta = " • ".join([x for x in [n.npc, n.location, n.emotion] if x])
        st.caption(meta)
    st.write(n.text)
    if n.gm_notes:
        show_gm = st.checkbox("Show GM notes", key="pb_show_gm")
        if show_gm:
            st.info(n.gm_notes)

    st.markdown("---")
    if not n.choices:
        st.success("End of branch.")
        return

    cols = st.columns(max(1, min(3, len(n.choices))))
    for i, ch in enumerate(n.choices):
        with cols[i % len(cols)]:
            label = ch.text
            if ch.gate:
                label += f"  [{ch.gate}]"
            if st.button(label, key=f"pb_{current_id}_{i}"):
                st.session_state.ui["playback_node_id"] = ch.target_id
                hist = st.session_state.ui.get("playback_history", [])
                hist.append(ch.target_id)
                st.session_state.ui["playback_history"] = hist
                st.rerun()

    playback_hist = [
        (nid, story.nodes[nid].title)
        for nid in st.session_state.ui.get("playback_history", [])
        if nid in story.nodes
    ]
    if playback_hist:
        st.markdown("#### Path so far")
        cols = st.columns(min(5, len(playback_hist)))
        for idx, (nid, title) in enumerate(playback_hist):
            col = cols[idx % len(cols)]
            if col.button(title, key=f"pb_hist_{idx}"):
                st.session_state.ui["playback_node_id"] = nid
                st.session_state.ui["playback_history"] = [
                    hid for hid, _ in playback_hist[: idx + 1]
                ]
                st.rerun()
        st.caption("Click any previous beat to jump back and branch from there.")



# ------------- Tab: Generators -------------
def tab_generators(story: Story):
    st.subheader("🧪 Generators — NPCs & Snippets (rule-based)")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### NPC Sketch Generator")
        arche = st.selectbox(
            "Archetype",
            [
                "Grizzled Guard",
                "Anxious Scholar",
                "Shifty Merchant",
                "Doomsayer Priest",
                "Eccentric Alchemist",
            ],
        )
        mood = st.select_slider(
            "Mood",
            options=["mournful", "wary", "neutral", "jovial", "zealous"],
        )
        quirk = st.selectbox(
            "Quirk",
            [
                "collects cursed spoons",
                "forgets nouns",
                "speaks to shadows",
                "overly polite",
                "won't touch coins",
            ],
        )
        btn = st.button("✨ Generate NPC")
        if btn:
            name = {
                "Grizzled Guard": "Sergeant Thorne",
                "Anxious Scholar": "Perrin of the Third Wing",
                "Shifty Merchant": "Velka 'Two-Ledgers'",
                "Doomsayer Priest": "Father Iksor",
                "Eccentric Alchemist": "Mottle Fizzwhisk",
            }[arche]
            snippet = f"{name}, a {arche.lower()}, looks {mood}. They {quirk}."
            st.write(snippet)
            if st.button("➕ Add as Node"):
                nid = add_node(
                    story,
                    title=name,
                    text=f"{snippet}\n\n'…'",
                    npc=name,
                    emotion=mood,
                )
                st.success(f"Added node: {name} ({nid[:8]})")

    with col2:
        st.markdown("#### Scene Flavor Generator")
        setting = st.selectbox(
            "Setting", ["Tavern", "Forest", "Ruins", "Cave", "City Night"]
        )
        tone = st.selectbox(
            "Tone", ["Cosmic Absurd", "Low Humor", "Dread", "Heroic", "Whimsical"]
        )
        if st.button("✨ Generate Scene"):
            base = {
                "Tavern": "The hearth crackles like a creature clearing its throat.",
                "Forest": "The trees lean in, like gossiping aunties with mossy hands.",
                "Ruins": "Stone arches remember names no mouth can pronounce.",
                "Cave": "Drips count seconds in a calendar no one respects.",
                "City Night": "Lanterns blink like tired gods on break.",
            }[setting]
            spice = {
                "Cosmic Absurd": "Somewhere, a star laughs at its own joke.",
                "Low Humor": "A stool wobbles with misplaced dignity.",
                "Dread": "Every shadow waits like a held breath.",
                "Heroic": "Even the dust looks ready to rise to the call.",
                "Whimsical": "Cats conduct moonlight with their tails.",
            }[tone]
            text = f"{base} {spice}"
            st.write(text)
            if st.button("➕ Add as Node", key="add_scene"):
                nid = add_node(
                    story,
                    title=f"{setting} Scene",
                    text=text,
                    location=setting,
                    emotion=tone,
                )
                st.success(f"Added node: {setting} Scene ({nid[:8]})")

def tab_ai(story: Story):
    st.subheader("🧠 AI Story Assistant (BranchWeaver)")

    client = get_openai_client()
    if client is None:
        st.warning(
            "No OpenAI API key found. To enable the AI assistant, set "
            "`st.secrets['openai']['api_key']` in your Streamlit Cloud settings."
        )
        st.stop()

    # Which mode?
    mode = st.radio(
        "What would you like the AI to do?",
        [
            "Generate a new branching sequence",
            "Expand the selected node",
            "Rewrite the selected node",
        ],
        key="ai_mode",
    )

    # Show current selection (if any)
    sel_id = st.session_state.ui.get("selected_node_id")
    sel_node = story.nodes.get(sel_id) if sel_id and sel_id in story.nodes else None

    with st.expander("Current selection & story context", expanded=False):
        if sel_node:
            st.markdown(f"**Selected node:** `{sel_node.title}` (`{sel_node.id[:8]}`)")
            st.write(sel_node.text or "_(no text)_")
            if sel_node.gm_notes:
                st.caption("GM notes: " + sel_node.gm_notes)
        else:
            st.caption("No node selected yet in the Branch Editor. The AI can still generate standalone content.")

        if st.checkbox("Show condensed story context (what the AI will see)", value=False, key="show_ai_context"):
            ctx = build_story_context(story)
            st.text_area("Story context sent to the AI (read-only)", value=ctx, height=220)

    # Prompt area depends on mode
    if mode == "Generate a new branching sequence":
        st.markdown("### 1) Describe the new sequence you want")
        prompt = st.text_area(
            "Describe what should happen (e.g. 'Return to Phandalin after Grol, with Toblen being weird about the haunting, Sister Garaele's odd energy, and hooks to Thundertree and Wave Echo Cave.')",
            height=140,
            key="ai_new_branch_prompt",
        )

        attach_to_existing = st.checkbox(
            "Attach this new sequence as a choice from the currently selected node (if any)",
            value=bool(sel_node),
            disabled=sel_node is None,
            key="ai_new_branch_attach",
        )
        attach_choice_text = ""
        if attach_to_existing and sel_node:
            attach_choice_text = st.text_input(
                "Text of the new choice on the selected node",
                value="Follow this thread…",
                key="ai_new_branch_choice_text",
            )

        if st.button("✨ Generate Branch with AI", key="ai_new_branch_go"):
            if not prompt.strip():
                st.error("Please describe the sequence you want.")
            else:
                with st.spinner("Talking to the eldritch script goblin…"):
                    ctx = build_story_context(story)
                    system_msg = (
                        "You are BranchWeaver, an assistant for creating branching D&D scenes. "
                        "You must respond ONLY with JSON in this exact format:\n\n"
                        "{\n"
                        '  "nodes": [\n'
                        "    {\n"
                        '      "title": "short scene or beat title",\n'
                        '      "text": "read-aloud style or narrative text",\n'
                        '      "npc": "main speaking NPC (or empty string)",\n'
                        '      "location": "where this happens",\n'
                        '      "emotion": "overall tone or emotion keyword",\n'
                        '      "tags": ["keyword1", "keyword2"],\n'
                        '      "gm_notes": "hidden notes for the DM: intentions, secrets, tactics, etc.",\n'
                        '      "choices": [\n'
                        "        {\n"
                        '          "text": "choice the players can make",\n'
                        '          "gate": "optional requirement like Skill DC or condition (or empty string)",\n'
                        '          "tags": ["optional", "tags", "for", "this", "choice"],\n'
                        '          "target_title": "title of another node from this same nodes list that this choice leads to (or empty string if this ends here)"\n'
                        "        }\n"
                        "      ]\n"
                        "    }\n"
                        "  ]\n"
                        "}\n\n"
                        "Do not include any explanation, only the JSON object."
                    )

                    user_msg = (
                        "Here is the existing campaign context:\n"
                        f"{ctx}\n\n"
                        "Now, based on that campaign, create a new branching sequence as requested.\n\n"
                        f"Request from DM:\n{prompt}\n\n"
                        "Remember to keep the tone consistent with the context (cosmic absurdity, streaks of humor, and the established NPC quirks)."
                    )

                    try:
                        resp = client.chat.completions.create(
                            model="gpt-4.1-mini",
                            messages=[
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": user_msg},
                            ],
                        )
                        raw = resp.choices[0].message.content.strip()
                        st.session_state["ai_last_raw_json"] = raw
                        st.success(
                            "AI branch generated. Review the JSON in the section below, then apply it to the story."
                        )
                    except Exception as e:
                        st.error(f"OpenAI call failed: {e}")

        # Allow applying the most recent AI branch even after a rerun
        if st.session_state.get("ai_last_raw_json"):
            st.markdown("### 2) Review & apply the last generated branch")
            st.code(st.session_state["ai_last_raw_json"], language="json")

            if st.button("✅ Apply these nodes to the story", key="ai_new_branch_apply"):
                try:
                    parent_id = sel_node.id if (attach_to_existing and sel_node) else None
                    new_ids = apply_ai_nodes_to_story(
                        story,
                        st.session_state["ai_last_raw_json"],
                        attach_parent_id=parent_id,
                        attach_choice_text=attach_choice_text if parent_id else None,
                    )
                    # Move selection to the first new node
                    if new_ids:
                        st.session_state.ui["selected_node_id"] = new_ids[0]
                    st.success(f"Added {len(new_ids)} new nodes to the story.")
                    st.session_state.pop("ai_last_raw_json", None)
                except Exception as e:
                    st.error(f"Failed to apply branch JSON: {e}")

    elif mode == "Expand the selected node":
        st.markdown("### 1) Expand the current node")
        if not sel_node:
            st.info("Select a node in the Branch Editor first to expand it.")
            return

        expand_prompt = st.text_area(
            "What kind of expansion do you want? (e.g. 'Add more eerie tavern details and give 2–3 interesting choices, one toward Thundertree and one toward Wave Echo Cave.')",
            height=120,
            key="ai_expand_prompt",
        )

        if st.button("✨ Propose expansion with AI", key="ai_expand_go"):
            if not expand_prompt.strip():
                st.error("Please describe how you'd like to expand this node.")
            else:
                with st.spinner("Letting the node grow extra tentacles…"):
                    ctx = build_story_context(story)
                    system_msg = (
                        "You are BranchWeaver, an assistant for expanding D&D story nodes. "
                        "Respond with JSON describing an updated version of THIS SINGLE NODE only, "
                        "in the same JSON structure as a single element inside the 'nodes' list:\n\n"
                        "{\n"
                        '  "title": "...",\n'
                        '  "text": "...",\n'
                        '  "npc": "...",\n'
                        '  "location": "...",\n'
                        '  "emotion": "...",\n'
                        '  "tags": ["..."],\n'
                        '  "gm_notes": "...",\n'
                        '  "choices": [\n'
                        "    {\n"
                        '      "text": "...",\n'
                        '      "gate": "...",\n'
                        '      "tags": ["..."],\n'
                        '      "target_title": ""  // leave blank to let the DM wire it\n'
                        "    }\n"
                        "  ]\n"
                        "}\n\n"
                        "Do not wrap this in a 'nodes' array. Return only the JSON object for this node."
                    )

                    user_msg = (
                        "Existing campaign context:\n"
                        f"{ctx}\n\n"
                        "Node to expand:\n"
                        f"Title: {sel_node.title}\n"
                        f"NPC: {sel_node.npc}\n"
                        f"Location: {sel_node.location}\n"
                        f"Emotion: {sel_node.emotion}\n"
                        f"Tags: {sel_node.tags}\n"
                        f"GM notes: {sel_node.gm_notes}\n"
                        f"Text:\n{sel_node.text}\n\n"
                        f"DM expansion request:\n{expand_prompt}\n"
                    )

                    try:
                        resp = client.chat.completions.create(
                            model="gpt-4.1-mini",
                            messages=[
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": user_msg},
                            ],
                        )
                        raw = resp.choices[0].message.content.strip()
                        st.session_state["ai_last_expand_json"] = raw
                        st.success("AI proposed an expanded version of this node. Review below.")
                    except Exception as e:
                        st.error(f"OpenAI call failed: {e}")

        if st.session_state.get("ai_last_expand_json"):
            st.markdown("### 2) Review & apply the last expansion proposal")
            st.code(st.session_state["ai_last_expand_json"], language="json")

            if st.button("✅ Apply expansion to this node", key="ai_expand_apply"):
                try:
                    nd = json.loads(st.session_state["ai_last_expand_json"])
                    sel_node.title = nd.get("title", sel_node.title)
                    sel_node.text = nd.get("text", sel_node.text)
                    sel_node.npc = nd.get("npc", sel_node.npc)
                    sel_node.location = nd.get("location", sel_node.location)
                    sel_node.emotion = nd.get("emotion", sel_node.emotion)
                    sel_node.tags = nd.get("tags", sel_node.tags)
                    sel_node.gm_notes = nd.get("gm_notes", sel_node.gm_notes)
                    sel_node.choices = []
                    for ch_def in nd.get("choices", []) or []:
                        sel_node.choices.append(
                            Choice(
                                text=ch_def.get("text", ""),
                                target_id="",  # DM can wire later in editor
                                tags=ch_def.get("tags", []) or [],
                                gate=ch_def.get("gate", ""),
                            )
                        )
                    st.success("Node updated with AI expansion.")
                    st.session_state.pop("ai_last_expand_json", None)
                except Exception as e:
                    st.error(f"Failed to apply expansion JSON: {e}")

    elif mode == "Rewrite the selected node":
        st.markdown("### 1) Rewrite the current node for tone or clarity")
        if not sel_node:
            st.info("Select a node in the Branch Editor first to rewrite it.")
            return

        rewrite_style = st.text_input(
            "Describe how to rewrite this node (e.g. 'More ominous, less silly' or 'Lean into Monty Python absurdism')",
            value="Keep the same content but sharpen the cosmic-dread-meets-absurdity vibe.",
            key="ai_rewrite_style",
        )

        if st.button("✨ Suggest rewrite", key="ai_rewrite_go"):
            with st.spinner("Polishing the monologue…"):
                ctx = build_story_context(story)
                system_msg = (
                    "You are BranchWeaver, an assistant for rewriting D&D story text for a DM. "
                    "You will be given one node and a rewrite style request. "
                    "Respond ONLY with a JSON object:\n\n"
                    "{\n"
                    '  "text": "rewritten node text",\n'
                    '  "gm_notes": "optional updated GM notes (or copy the old ones if mostly unchanged)"\n'
                    "}\n\n"
                    "Do not include any explanation, only the JSON."
                )


                user_msg = (
                    "Campaign context (for tone only):\n"
                    f"{ctx}\n\n"
                    "Node to rewrite:\n"
                    f"Title: {sel_node.title}\n"
                    f"GM notes: {sel_node.gm_notes}\n"
                    f"Text:\n{sel_node.text}\n\n"
                    f"Rewrite request:\n{rewrite_style}\n"
                )

                try:
                    resp = client.chat.completions.create(
                        model="gpt-4.1-mini",
                        messages=[
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                        ],
                    )
                    raw = resp.choices[0].message.content.strip()
                    st.session_state["ai_last_rewrite_json"] = raw
                    st.success("AI suggested a rewrite. Review below.")
                except Exception as e:
                    st.error(f"OpenAI call failed: {e}")

        if st.session_state.get("ai_last_rewrite_json"):
            st.markdown("### 2) Review & apply the last rewrite")
            st.code(st.session_state["ai_last_rewrite_json"], language="json")

            if st.button("✅ Apply rewrite", key="ai_rewrite_apply"):
                try:
                    nd = json.loads(st.session_state["ai_last_rewrite_json"])
                    sel_node.text = nd.get("text", sel_node.text)
                    if "gm_notes" in nd:
                        sel_node.gm_notes = nd.get("gm_notes", sel_node.gm_notes)
                    st.success("Node text updated.")
                    st.session_state.pop("ai_last_rewrite_json", None)
                except Exception as e:
                    st.error(f"Failed to apply rewrite JSON: {e}")



# ------------- Tab: World State -------------
def tab_world_state(story: Story):
    st.subheader("🌍 World State — Tags, NPCs, Locations")
    npcs = sorted({n.npc for n in story.nodes.values() if n.npc})
    locs = sorted({n.location for n in story.nodes.values() if n.location})
    tags = sorted({t for n in story.nodes.values() for t in n.tags})

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("#### NPCs")
        for x in npcs:
            st.write("• ", x)
    with c2:
        st.markdown("#### Locations")
        for x in locs:
            st.write("• ", x)
    with c3:
        st.markdown("#### Tags")
        for x in tags:
            st.write("• ", x)

    st.markdown("---")
    st.markdown("#### Quick Create")
    title = st.text_input("Title", key="ws_title")
    text = st.text_area("Text", key="ws_text", height=100)
    colx, coly, colz = st.columns(3)
    with colx:
        npc = st.text_input("NPC", key="ws_npc")
    with coly:
        loc = st.text_input("Location", key="ws_loc")
    with colz:
        emo = st.text_input("Emotion", key="ws_emo")
    ttags = st.text_input("Tags (comma-separated)", key="ws_tags")
    if st.button("➕ Add Node", key="ws_add"):
        nid = add_node(
            story,
            title=title or "Untitled",
            text=text,
            npc=npc,
            location=loc,
            emotion=emo,
            tags=[t.strip() for t in ttags.split(",") if t.strip()],
        )
        st.success(f"Added node {nid[:8]}")


# ------------- Tab: Import/Export -------------
def tab_io(story: Story):
    st.subheader("📦 Import / Export")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Export JSON")
        json_bytes = story_to_json_bytes(story)
        st.download_button(
            "⬇️ Download story.json",
            data=json_bytes,
            file_name="branchweaver_story.json",
            mime="application/json",
        )
        st.caption("(The download is UTF-8 JSON—safe to re-import later.)")


        st.markdown("#### Export Markdown")
        md_simple = export_markdown(story, detailed=False)
        st.download_button(
            "⬇️ Summary.md", data=md_simple, file_name="story_summary.md"
        )
        md_d = export_markdown(story, detailed=True)
        st.download_button(
            "⬇️ Detailed.md", data=md_d, file_name="story_detailed.md"
        )

    with col2:
        st.markdown("#### Import JSON")
        up = st.file_uploader("Upload BranchWeaver JSON", type=["json"], key="import_json")
    
        if up is not None and not st.session_state.has_imported:
            try:
                data = up.read().decode("utf-8")
                st.session_state.story = story_from_json(data)
    
                # Reset UI selection / playback to new story
                story = st.session_state.story
                first_id = next(iter(story.nodes.keys()), None)
                st.session_state.ui["selected_node_id"] = first_id
                st.session_state.ui["playback_node_id"] = first_id
                st.session_state.ui["playback_history"] = [first_id] if first_id else []
    
                st.session_state.has_imported = True   # prevent repeat imports
    
                st.success("Imported story.")
                st.rerun()
    
            except Exception as e:
                st.error(f"Failed to import: {e}")
    
        # If user clears the file uploader (clicks X), allow importing again
        if up is None and st.session_state.has_imported:
            st.session_state.has_imported = False





# ------------- Tab: Settings -------------
def tab_settings(story: Story):
    st.subheader("⚙️ Settings")
    st.caption("Display and defaults.")
    st.session_state.ui["tone_preset"] = st.selectbox(
        "Default Tone Preset",
        ["Cosmic Absurd", "Dread", "Heroic", "Whimsical", "Neutral"],
        index=[
            "Cosmic Absurd",
            "Dread",
            "Heroic",
            "Whimsical",
            "Neutral",
        ].index(st.session_state.ui["tone_preset"]),
    )
    st.write("Color Palette (fixed)")
    st.color_picker("Example Color", COLORS[0], key="dummy_color_picker")
    st.info(
        "For now, colors are auto-assigned per value (NPC/Location/Emotion). "
        "Advanced themes can be added later."
    )


# -------------------------------
# Main App
# -------------------------------
def main():
    ensure_state()

    # One-time autosave load attempt
    if "autosave_checked" not in st.session_state:
        try_autoload()
        st.session_state.autosave_checked = True

    story: Story = st.session_state.story

    sidebar_project(story)

    tabs = st.tabs([
        "📘 Overview",
        "🧩 Branch Editor",
        "🕸️ Visualizer",
        "🎬 Playback",
        "🧪 Generators",
        "🧠 AI Assistant",
        "🌍 World State",
        "📦 Import / Export",
        "⚙️ Settings",
    ])


    with tabs[0]:
        tab_overview(story)
    with tabs[1]:
        tab_editor(story)
    with tabs[2]:
        tab_visualizer(story)
    with tabs[3]:
        tab_playback(story)
    with tabs[4]:
        tab_generators(story)
    with tabs[5]:
        tab_ai(story)          # 👈 NEW
    with tabs[6]:
        tab_world_state(story)
    with tabs[7]:
        tab_io(story)
    with tabs[8]:
        tab_settings(story)


    # Auto-save story on each run
    autosave(story)


if __name__ == "__main__":
    main()
