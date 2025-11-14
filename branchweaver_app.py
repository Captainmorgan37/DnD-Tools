# BranchWeaver — Interactive Story & Dialogue Planner
# Streamlit full-featured app (single-file)
#
# Key features
# - Tabs: Overview • Branch Editor • Visualizer • Playback • Generators • World State • Import/Export • Settings
# - Node model: id, title, text, tags, npc, location, emotion, gm_notes, choices[{text, target_id, tags, test/gate}]
# - Create, edit, duplicate, delete nodes and choices
# - Graphviz visual map with filters and color/shape by type
# - Playback mode to rehearse a conversation/path
# - NPC/Scene snippet generators (rule-based, no external APIs required)
# - JSON import/export + Markdown export (summary or detailed)
# - Auto-save to session; optional local seed story
#
# Usage:
#   streamlit run branchweaver_app.py

from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple

import streamlit as st
import graphviz

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
# Helpers
# -------------------------------

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
            "tag_filter": [],
            "show_gm": True,
            "color_by": "npc",  # npc | location | emotion | none
            "shape_by": "type",  # type | none (type currently: start vs normal)
            "playback_node_id": None,
            "playback_history": [],  # list of node_ids visited
            "tone_preset": "Cosmic Absurd",
        }


import os

def try_autoload():
    if os.path.exists("story_autosave.json"):
        try:
            with open("story_autosave.json", "r", encoding="utf-8") as f:
                data = f.read()
            st.session_state.story = story_from_json(data)
            return True
        except:
            return False
    return False


def autosave(story: Story):
    try:
        with open("story_autosave.json", "w", encoding="utf-8") as f:
            f.write(story_to_json(story))
    except:
        pass



def node_to_label(n: Node, show_gm: bool = False) -> str:
    """HTML-like label for Graphviz."""
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
    "#fa9fb5", "#a1d99b", "#c6dbef", "#fdae6b", "#bcbddc",
    "#9ecae1", "#fcae91", "#c7e9c0", "#dadaeb", "#cbc9e2",
]


def color_for_value(value: str) -> str:
    if not value:
        return "#dddddd"
    # Deterministic mapping
    idx = abs(hash(value)) % len(COLORS)
    return COLORS[idx]


def add_node(story: Story, *, title: str, text: str, npc: str = "", location: str = "", emotion: str = "", tags: Optional[List[str]] = None, gm_notes: str = "") -> str:
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
        # Remove inbound references
        for n in story.nodes.values():
            n.choices = [c for c in n.choices if c.target_id != node_id]
        # Remove self
        del story.nodes[node_id]
        # Fix start
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
        choices=[Choice(text=c.text, target_id=c.target_id, tags=list(c.tags), gate=c.gate) for c in n.choices],
    )
    story.nodes[new_id] = new_node
    return new_id


def story_to_json(story: Story) -> str:
    def _encode(obj):
        if isinstance(obj, Story):
            return {
                "title": obj.title,
                "description": obj.description,
                "start_node_id": obj.start_node_id,
                "nodes": {k: _encode(v) for k, v in obj.nodes.items()},
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
        raise TypeError("Unsupported type")

    return json.dumps(_encode(story), indent=2)


def story_from_json(s: str) -> Story:
    data = json.loads(s)
    nodes: Dict[str, Node] = {}
    for nid, nd in data.get("nodes", {}).items():
        nodes[nid] = Node(
            id=nd["id"],
            title=nd.get("title", "(untitled)"),
            text=nd.get("text", ""),
            npc=nd.get("npc", ""),
            location=nd.get("location", ""),
            emotion=nd.get("emotion", ""),
            tags=nd.get("tags", []),
            gm_notes=nd.get("gm_notes", ""),
            choices=[Choice(**c) for c in nd.get("choices", [])],
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
    # Try to place start first
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
                {"text": "Parley about the map", "target": "Trade for the Map"}
            ]
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
                {"text": "Reveal a secret", "target": "Whispers in the Dark", "gate": "Deception DC14"}
            ]
        },
        {
            "title": "Unholy Strength Stirs",
            "text": "Grol's flesh splits; eyes bloom like ulcers. The crowd gasps. Shadows thicken.",
            "npc": "King Grol",
            "location": "Chamber 5",
            "emotion": "wrathful",
            "tags": ["phase2", "combat"],
            "gm_notes": "Phase 2 boosts; psychic bleed."
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
                {"text": "Offer a cursed relic", "target": "The Relic Hungers", "gate": "Arcana DC13"}
            ]
        }
    ]
}


def load_seed(story: Story, seed=SEED_STORY):
    title = seed.get("title", "Seed Story")
    description = seed.get("description", "")
    id_map = {}
    # Create nodes
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
        id_map[entry["title"]] = nid
    # Wire choices (safe, strict Choice construction)
    for entry in seed.get("nodes", []):
        src_id = id_map.get(entry.get("title", ""))
        if not src_id:
            continue
    
        for ch in entry.get("choices", []):
            tgt_title = ch.get("target", "")
            tgt_id = id_map.get(tgt_title, "")
    
            if not tgt_id:
                # Skip malformed or unmatched targets
                continue
    
            story.nodes[src_id].choices.append(
                Choice(
                    text=ch.get("text", ""),
                    target_id=tgt_id,
                    tags=list(ch.get("tags", [])),
                    gate=ch.get("gate", "")
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
        st.sidebar.success("Seed loaded.")

    st.sidebar.markdown("---")
    st.sidebar.subheader("🔎 Graph Filters")
    st.session_state.ui["filter_text"] = st.sidebar.text_input("Search (title/text)", st.session_state.ui["filter_text"])
    st.session_state.ui["show_gm"] = st.sidebar.checkbox("Show GM notes on nodes", st.session_state.ui["show_gm"])
    st.session_state.ui["color_by"] = st.sidebar.selectbox("Color by", ["npc", "location", "emotion", "none"], index=["npc","location","emotion","none"].index(st.session_state.ui["color_by"]))
    st.session_state.ui["shape_by"] = st.sidebar.selectbox("Shape by", ["type", "none"], index=["type", "none"].index(st.session_state.ui["shape_by"]))


# ------------- Tab: Overview -------------

def tab_overview(story: Story):
    c1, c2 = st.columns([2, 3])
    with c1:
        st.markdown(f"### 📘 {story.title}")
        st.write(story.description or "No description yet.")
        st.caption(f"Nodes: {len(story.nodes)} | Start: {story.start_node_id[:8] if story.start_node_id else 'n/a'}")
        if st.button("➕ Quick Add 'Beat' Node"):
            nid = add_node(story, title="New Beat", text="Describe the beat…")
            st.session_state.ui["selected_node_id"] = nid
            st.experimental_rerun()

    with c2:
        st.markdown("### 🧾 Recently Edited")
        # Show a few nodes by latest creation order (approx by dict order)
        recent = list(story.nodes.values())[-5:]
        if not recent:
            st.info("No nodes yet. Add one in the Branch Editor tab.")
        for n in reversed(recent):
            with st.expander(f"{n.title}  ·  {n.id[:8]}"):
                st.write(n.text)
                meta = []
                if n.npc: meta.append(f"NPC: {n.npc}")
                if n.location: meta.append(f"@ {n.location}")
                if n.emotion: meta.append(f"[{n.emotion}]")
                if meta:
                    st.caption(" • ".join(meta))
                if n.gm_notes:
                    st.caption("GM: " + n.gm_notes)
                if st.button("Edit This Node", key=f"ov_edit_{n.id}"):
                    st.session_state.ui["selected_node_id"] = n.id
                    st.experimental_rerun()


# ------------- Tab: Branch Editor -------------

def tab_editor(story: Story):
    left, right = st.columns([2, 3])

    with left:
        st.subheader("🧩 Nodes")
        # Search & list
        q = st.session_state.ui["filter_text"].lower().strip()
        node_items = list(story.nodes.items())
        if q:
            node_items = [kv for kv in node_items if (q in kv[1].title.lower() or q in kv[1].text.lower())]
        # sorter by title
        node_items.sort(key=lambda kv: kv[1].title.lower())

        options = [f"{v.title}  ·  {k[:8]}" for k, v in node_items]
        idx = 0
        preselect_id = st.session_state.ui.get("selected_node_id")
        if preselect_id:
            try:
                idx = [k for k,_ in node_items].index(preselect_id)
            except ValueError:
                idx = 0
        sel = st.selectbox("Select a node", options, index=idx if options else 0)
        selected_id = None
        if options:
            selected_id = node_items[options.index(sel)][0]
            st.session_state.ui["selected_node_id"] = selected_id

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("➕ New", use_container_width=True):
                nid = add_node(story, title="New Node", text="", npc="", location="", emotion="")
                st.session_state.ui["selected_node_id"] = nid
                st.experimental_rerun()
        with c2:
            if selected_id and st.button("📄 Duplicate", use_container_width=True):
                new_id = duplicate_node(story, selected_id)
                st.session_state.ui["selected_node_id"] = new_id
                st.experimental_rerun()
        with c3:
            if selected_id and st.button("⭐ Make Start", use_container_width=True):
                story.start_node_id = selected_id
        with c4:
            if selected_id and st.button("🗑️ Delete", use_container_width=True):
                delete_node(story, selected_id)
                st.session_state.ui["selected_node_id"] = None
                st.experimental_rerun()

    with right:
        st.subheader("✍️ Node Editor")
        if not selected_id:
            st.info("Select or create a node to edit.")
            return
        node = story.nodes[selected_id]

        with st.form(key=f"edit_{selected_id}"):
            cA, cB = st.columns([2, 1])
            with cA:
                node.title = st.text_input("Title / Speaker", value=node.title)
                node.text = st.text_area("Scene / Dialogue Text", value=node.text, height=160)
                node.gm_notes = st.text_area("GM Notes (hidden)", value=node.gm_notes, height=80)
            with cB:
                node.npc = st.text_input("NPC", value=node.npc)
                node.location = st.text_input("Location", value=node.location)
                node.emotion = st.text_input("Emotion", value=node.emotion)
                tag_str = st.text_input("Tags (comma-separated)", value=", ".join(node.tags))
                node.tags = [t.strip() for t in tag_str.split(",") if t.strip()]

            st.markdown("---")
            st.markdown("#### Choices / Branches")
            # Edit choices
            for i, ch in enumerate(list(node.choices)):
                with st.expander(f"Choice {i+1}: {ch.text or '(untitled)'}", expanded=False):
                    ch.text = st.text_input("Choice text", value=ch.text, key=f"ct_{selected_id}_{i}")
                    ch.gate = st.text_input("Gate/Requirement (optional)", value=ch.gate, key=f"gate_{selected_id}_{i}")
                    ch.tags = [t.strip() for t in st.text_input("Tags (comma-separated)", value=", ".join(ch.tags), key=f"ctags_{selected_id}_{i}").split(",") if t.strip()]

                    # Target selector
                    choices_list = [f"{n.title} · {nid[:8]}" for nid, n in story.nodes.items()]
                    # current selection index
                    def _index_for_target(tid: str) -> int:
                        ids = list(story.nodes.keys())
                        try:
                            return ids.index(tid)
                        except ValueError:
                            return 0
                    current_idx = _index_for_target(ch.target_id)
                    sel_target = st.selectbox("Leads to node", choices_list, index=current_idx, key=f"sel_{selected_id}_{i}")
                    ch.target_id = list(story.nodes.keys())[choices_list.index(sel_target)]

                    col_rm, col_up, col_dn = st.columns([1,1,1])
                    with col_rm:
                        if st.button("Remove", key=f"rm_{selected_id}_{i}"):
                            node.choices.pop(i)
                            st.experimental_rerun()
                    with col_up:
                        if st.button("↑ Move", key=f"up_{selected_id}_{i}") and i>0:
                            node.choices[i-1], node.choices[i] = node.choices[i], node.choices[i-1]
                            st.experimental_rerun()
                    with col_dn:
                        if st.button("↓ Move", key=f"dn_{selected_id}_{i}") and i < len(node.choices)-1:
                            node.choices[i+1], node.choices[i] = node.choices[i], node.choices[i+1]
                            st.experimental_rerun()

            # Add new choice
            st.markdown("**Add Choice**")
            new_c_text = st.text_input("New choice text", key=f"newct_{selected_id}")
            col_t1, col_t2 = st.columns([2,1])
            with col_t1:
                tar_sel = st.selectbox("Target node", [f"{n.title} · {nid[:8]}" for nid, n in story.nodes.items()], key=f"newtar_{selected_id}")
            with col_t2:
                req = st.text_input("Gate (opt.)", key=f"newgate_{selected_id}")
            if st.form_submit_button("💾 Save Node & Choices"):
                if new_c_text:
                    target_id = list(story.nodes.keys())[list(story.nodes.keys()).index(tar_sel.split(" · ")[-1])]
                st.success("Saved.")

            # Because of the way we saved, now append the new choice if provided
            if new_c_text:
                target_id = list(story.nodes.keys())[ [f"{n.title} · {nid[:8]}" for nid, n in story.nodes.items()].index(tar_sel) ]
                node.choices.append(Choice(text=new_c_text, target_id=target_id, gate=req))
                st.experimental_rerun()


# ------------- Tab: Visualizer -------------

def tab_visualizer(story: Story):
    st.subheader("🕸️ Branch Map")
    q = st.session_state.ui["filter_text"].lower().strip()
    show_gm = st.session_state.ui["show_gm"]
    color_by = st.session_state.ui["color_by"]
    shape_by = st.session_state.ui["shape_by"]

    dot = graphviz.Digraph("branchweaver", format="png")
    dot.attr(rankdir="LR")

    # Build nodes
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
        # shape
        shape = "oval"
        if shape_by == "type":
            if story.start_node_id == nid:
                shape = "doublecircle"
            else:
                shape = "box"
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

    st.graphviz_chart(dot, use_container_width=True)

    # Export DOT
    st.download_button(
        label="⬇️ Download DOT",
        data=dot.source,
        file_name="branchweaver_graph.dot",
        mime="text/plain",
    )


# ------------- Tab: Playback -------------




def tab_playback(story: Story):
    st.subheader("🎬 Playback — Rehearse a Path")
    if not story.nodes:
        st.info("No nodes in the story yet. Add some in the Branch Editor.")
        return
    
    ids = list(story.nodes.keys())
    if not ids:
        st.warning("No nodes available for playback.")
        return
    # Pick a starting node
    ids = list(story.nodes.keys())
    labels = [f"{story.nodes[i].title} · {i[:8]}" for i in ids]
    # Pick a starting node safely
    start_idx = 0
    if story.start_node_id in ids:
        start_idx = ids.index(story.start_node_id)
    else:
        # Auto-fix broken start_node_id
        story.start_node_id = ids[0]
    
    start_label = st.selectbox("Start at", labels, index=start_idx)

    start_id = ids[labels.index(start_label)]

    # Reset controls
    colx, coly = st.columns([1,1])
    with colx:
        if st.button("🔁 Restart"):
            st.session_state.ui["playback_node_id"] = start_id
            st.session_state.ui["playback_history"] = [start_id]
    with coly:
        if st.button("⬅️ Step Back"):
            hist = st.session_state.ui.get("playback_history", [])
            if len(hist) > 1:
                hist.pop()
                st.session_state.ui["playback_node_id"] = hist[-1]
                st.session_state.ui["playback_history"] = hist

    # Initialize
    # Ensure current playback node is valid
    curr = st.session_state.ui.get("playback_node_id")
    if curr not in ids:
        st.session_state.ui["playback_node_id"] = start_id
        st.session_state.ui["playback_history"] = [start_id]


    current_id = st.session_state.ui["playback_node_id"]
    if current_id not in story.nodes:
        st.warning("Current node missing.")
        return

    n = story.nodes[current_id]
    st.markdown(f"### {n.title}")
    if n.npc or n.location or n.emotion:
        meta = " • ".join([x for x in [n.npc, n.location, n.emotion] if x])
        st.caption(meta)
    st.write(n.text)
    if n.gm_notes and st.checkbox("Show GM notes", value=False):
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
                st.session_state.ui["playback_history"].append(ch.target_id)
                st.experimental_rerun()

    st.caption("History: " + " → ".join(story.nodes[x].title for x in st.session_state.ui["playback_history"]))


# ------------- Tab: Generators -------------

def tab_generators(story: Story):
    st.subheader("🧪 Generators — NPCs & Snippets (rule-based)")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### NPC Sketch Generator")
        arche = st.selectbox("Archetype", ["Grizzled Guard", "Anxious Scholar", "Shifty Merchant", "Doomsayer Priest", "Eccentric Alchemist"]) 
        mood = st.select_slider("Mood", options=["mournful", "wary", "neutral", "jovial", "zealous"])
        quirk = st.selectbox("Quirk", ["collects cursed spoons", "forgets nouns", "speaks to shadows", "overly polite", "won't touch coins"]) 
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
                nid = add_node(story, title=name, text=f"{snippet}\n\n'…'", npc=name, emotion=mood)
                st.success(f"Added node: {name} ({nid[:8]})")

    with col2:
        st.markdown("#### Scene Flavor Generator")
        setting = st.selectbox("Setting", ["Tavern", "Forest", "Ruins", "Cave", "City Night"])
        tone = st.selectbox("Tone", ["Cosmic Absurd", "Low Humor", "Dread", "Heroic", "Whimsical"]) 
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
                nid = add_node(story, title=f"{setting} Scene", text=text, location=setting, emotion=tone)
                st.success(f"Added node: {setting} Scene ({nid[:8]})")


# ------------- Tab: World State -------------

def tab_world_state(story: Story):
    st.subheader("🌍 World State — Tags, NPCs, Locations")
    # Simple summaries extracted from nodes
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
        nid = add_node(story, title=title or "Untitled", text=text, npc=npc, location=loc, emotion=emo, tags=[t.strip() for t in ttags.split(",") if t.strip()])
        st.success(f"Added node {nid[:8]}")


# ------------- Tab: Import/Export -------------

def tab_io(story: Story):
    st.subheader("📦 Import / Export")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Export JSON")
        j = story_to_json(story)
        st.download_button("⬇️ Download story.json", data=j, file_name="branchweaver_story.json", mime="application/json")

        st.markdown("#### Export Markdown")
        md_simple = export_markdown(story, detailed=False)
        st.download_button("⬇️ Summary.md", data=md_simple, file_name="story_summary.md")
        md_d = export_markdown(story, detailed=True)
        st.download_button("⬇️ Detailed.md", data=md_d, file_name="story_detailed.md")

    with col2:
        st.markdown("#### Import JSON")
        up = st.file_uploader("Upload BranchWeaver JSON", type=["json"]) 
        if up is not None:
            try:
                data = up.read().decode("utf-8")
                st.session_state.story = story_from_json(data)
                st.success("Imported story.")
                st.experimental_rerun()
            except Exception as e:
                st.error(f"Failed to import: {e}")


# ------------- Tab: Settings -------------

def tab_settings(story: Story):
    st.subheader("⚙️ Settings")
    st.caption("Display and defaults.")
    st.session_state.ui["tone_preset"] = st.selectbox("Default Tone Preset", ["Cosmic Absurd", "Dread", "Heroic", "Whimsical", "Neutral"], index=["Cosmic Absurd", "Dread", "Heroic", "Whimsical", "Neutral"].index(st.session_state.ui["tone_preset"]))
    st.write("Color Palette (fixed)")
    st.color_picker("Example Color", COLORS[0], key="dummy_color_picker")
    st.info("For now, colors are auto-assigned per value (NPC/Location/Emotion). Advanced themes can be added later.")


# -------------------------------
# Main App
# -------------------------------

def main():
    ensure_state()
    story: Story = st.session_state.story

    # -------------------------------
    # Auto-load saved story if available
    # -------------------------------
    if "loaded_autosave" not in st.session_state:
        if try_autoload():
            st.session_state.loaded_autosave = True
        else:
            st.session_state.loaded_autosave = False


    # Sidebar + Project controls
    sidebar_project(story)

    tabs = st.tabs([
        "📘 Overview",
        "🧩 Branch Editor",
        "🕸️ Visualizer",
        "🎬 Playback",
        "🧪 Generators",
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
        tab_world_state(story)
    with tabs[6]:
        tab_io(story)
    with tabs[7]:
        tab_settings(story)

    # -------------------------------
    # Auto-save after each run
    # -------------------------------
    autosave(st.session_state.story)



if __name__ == "__main__":
    main()
