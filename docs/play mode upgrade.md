# BranchWeaver Play Mode — State Tracking & Runtime Engine

This document describes the **Play Mode with State Tracking** system for BranchWeaver, designed to create an interactive, visual-novel-style DM tool.

---

## Overview

Play Mode adds a *runtime layer* to BranchWeaver that tracks:

- The player's **current node**
- The **path taken** through the story
- **Flags** representing story choices & consequences
- **Inventory**
- **Visited nodes**
- **DM notes**
- **Conditional gating** for choices

This enables:
- AI-assisted continuation that respects prior decisions
- Exportable “canonical playthroughs”
- Live campaign running directly inside BranchWeaver

---

## Data Model

### StoryState

```python
@dataclass
class StoryState:
    current_node_id: str
    history: List[str] = field(default_factory=list)
    visited: Set[str] = field(default_factory=set)
    flags: Dict[str, Any] = field(default_factory=dict)
    inventory: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self):
        return {
            "current_node_id": self.current_node_id,
            "history": self.history,
            "visited": list(self.visited),
            "flags": self.flags,
            "inventory": self.inventory,
            "notes": self.notes,
        }
```

This object is stored inside Streamlit’s session state as:

```
st.session_state["play_state"]
```

---

## Gate Evaluation (Conditional Choices)

Nodes and choices support **gate expressions** such as:

- `"helped_toblen"`
- `"met_sildar and not betrayed_town"`
- `"reputation >= 3"`

Gate evaluation:

```python
def evaluate_gate(gate, flags):
    if not gate:
        return True
    return bool(eval(gate, {"__builtins__": {}}, flags))
```

---

## Play Mode Flow

### 1. Initialization

When starting a session:

- Set `current_node_id = story.start_node_id`
- Add that node to `history` and `visited`
- Reset flags, inventory, notes

A reset button fully clears the session state.

---

### 2. Scene View (Left Side)

Play Mode displays:

- Node title
- Metadata (NPC, location, emotion)
- Tags
- Main story text
- GM notes (in an expander)
- List of choices

Each choice becomes a button.

Clicking a choice:

- Adds current node to history
- Moves to the next node
- Registers it as visited
- Runs any future **node effects** or **choice effects** (optional extension)

---

### 3. State Sidebar (Right Side)

#### Flags  
- Lists all active flags
- Allows adding/removing flags

#### Inventory  
- Simple list of text items
- Add/remove via sidebar

#### Notes  
- DM-only session notes

#### Path Taken  
- Shows ordered list of node IDs & titles

#### Export Playthrough  
Downloads:

```json
{
  "story_title": "...",
  "path_node_ids": [...],
  "path_node_titles": [...],
  "flags": {...},
  "inventory": [...],
  "notes": "..."
}
```

---

## AI Integration

Play Mode provides a **canonical playthrough context**:

```json
{
  "story_title": "...",
  "current_node": "...",
  "history": [...],
  "visited": [...],
  "flags": {...},
  "inventory": [...],
  "notes": "..."
}
```

This lets AI generators create:

- Next-scene proposals
- Consequence-aware content
- NPC reactions based on flags
- Branch suggestions & expansions

Example use:

> “Given this context (flags, history, and current node), generate 3 next scenes that logically follow from the party’s actions.”

---

## Summary

This system adds:

- Visual novel–style gameplay  
- State-driven branching  
- Fully exportable session logs  
- AI-driven continuity  
- Conditional logic for branching scenes  
- A robust runtime separate from editing tools  

It transforms BranchWeaver from a *story mapper* into a **full DM campaign engine**.

