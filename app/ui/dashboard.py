from collections.abc import MutableMapping
from uuid import uuid4

import streamlit as st

from app.schemas.task import TaskResponse
from app.ui.api_client import APIClient, APIError

TASK_STATUSES = ("Pending", "In Progress", "Completed", "Cancelled")
TASK_PRIORITIES = ("High", "Medium", "Low")


def initialize_session_state(state: MutableMapping[str, object]) -> None:
    state.setdefault("conversation_id", uuid4().hex)
    state.setdefault("messages", [])
    state.setdefault("is_sending", False)
    state.setdefault("tasks", None)
    state.setdefault("task_filter_key", None)
    state.setdefault("refresh_tasks", True)


def start_new_conversation(state: MutableMapping[str, object]) -> str:
    conversation_id = uuid4().hex
    state["conversation_id"] = conversation_id
    state["messages"] = []
    return conversation_id


def submit_message(
    client: APIClient,
    state: MutableMapping[str, object],
    message: str,
) -> bool:
    content = message.strip()
    if not content or state.get("is_sending"):
        return False

    messages = state["messages"]
    if not isinstance(messages, list):
        raise TypeError("Chat messages must be stored as a list.")
    user_message: dict[str, str] = {"role": "user", "content": content}
    messages.append(user_message)
    state["is_sending"] = True
    try:
        response = client.send_message(str(state["conversation_id"]), content)
    except APIError as exc:
        user_message["error"] = exc.message
        return False
    finally:
        state["is_sending"] = False

    messages.append({"role": "assistant", "content": response.response})
    state["refresh_tasks"] = True
    return True


def refresh_task_data(
    client: APIClient,
    state: MutableMapping[str, object],
    filters: dict[str, str | bool],
    *,
    force: bool = False,
) -> list[TaskResponse] | None:
    filter_key = tuple(sorted(filters.items()))
    if (
        force
        or state.get("refresh_tasks", True)
        or state.get("task_filter_key") != filter_key
    ):
        try:
            tasks = client.list_tasks(filters)
        except APIError as exc:
            state["tasks"] = None
            state["task_error"] = exc.message
            return None
        state["tasks"] = tasks
        state["task_filter_key"] = filter_key
        state["refresh_tasks"] = False
        state["task_error"] = None

    tasks = state.get("tasks")
    return tasks if isinstance(tasks, list) else None


def render_chat_messages(messages: list[dict[str, str]]) -> None:
    for message in messages:
        role = message["role"]
        with st.chat_message(role):
            st.markdown(message["content"])
            error = message.get("error")
            if error:
                st.error(error)


def _render_chat(client: APIClient, state: MutableMapping[str, object]) -> None:
    st.title("Chat")
    st.caption(
        "Ask your productivity assistant to organize or update tasks. "
        "Messages shown here are kept only in this browser session."
    )
    st.caption(
        "Conversation context lives in the backend process. It may be lost when "
        "the server restarts or routes requests to another worker."
    )

    if st.button("New conversation", type="secondary"):
        start_new_conversation(state)
        st.rerun()

    messages = state["messages"]
    if not isinstance(messages, list):
        raise TypeError("Chat messages must be stored as a list.")
    if messages:
        render_chat_messages(messages)
    else:
        st.info("Start a conversation to add, update, or review your tasks.")

    with st.form("chat_form", clear_on_submit=True):
        message = st.text_area(
            "Message",
            placeholder="For example: Add a high-priority task to prepare my report.",
            max_chars=10_000,
            height=96,
        )
        submitted = st.form_submit_button(
            "Send message",
            type="primary",
            disabled=bool(state.get("is_sending")),
        )

    if submitted:
        if not message.strip():
            st.warning("Enter a message before sending.")
        elif state.get("is_sending"):
            st.info("Your message is already being sent.")
        else:
            with st.spinner("Waiting for your assistant..."):
                submit_message(client, state, message)
            st.rerun()


def _task_filters() -> dict[str, str | bool]:
    st.title("Tasks")
    status_label = st.selectbox(
        "Status",
        ["All statuses", *TASK_STATUSES],
    )
    priority_label = st.selectbox(
        "Priority",
        ["All priorities", *TASK_PRIORITIES],
    )
    search = st.text_input(
        "Search tasks",
        placeholder="Title or description",
        max_chars=200,
    )
    overdue_only = st.checkbox("Overdue only")

    filters: dict[str, str | bool] = {}
    if status_label != "All statuses":
        filters["status"] = status_label
    if priority_label != "All priorities":
        filters["priority"] = priority_label
    if search.strip():
        filters["search"] = search.strip()
    if overdue_only:
        filters["overdue_only"] = True
    return filters


def _render_task(task: TaskResponse) -> None:
    with st.container(border=True):
        st.subheader(task.title)
        st.write(f"Status: {task.status.value}  ·  Priority: {task.priority.value}")
        if task.description:
            st.text(task.description)
        if task.due_date:
            st.caption(f"Due {task.due_date.strftime('%b %d, %Y %H:%M')}")
        else:
            st.caption("No due date")


def _render_tasks(client: APIClient, state: MutableMapping[str, object]) -> None:
    filters = _task_filters()
    refresh_clicked = st.button("Refresh tasks")
    if refresh_clicked:
        state["refresh_tasks"] = True

    with st.spinner("Loading tasks..."):
        tasks = refresh_task_data(
            client,
            state,
            filters,
            force=refresh_clicked,
        )

    error = state.get("task_error")
    if isinstance(error, str) and error:
        st.error(error)
        return
    if tasks is None:
        st.error("Task data could not be loaded.")
        return
    if not tasks:
        st.info("No tasks match these filters.")
        return

    st.caption(f"{len(tasks)} task{'s' if len(tasks) != 1 else ''}")
    for task in tasks:
        _render_task(task)


def main() -> None:
    st.set_page_config(
        page_title="AI Personal Task Manager",
        page_icon="✅",
        layout="wide",
    )
    try:
        client = APIClient()
    except APIError as exc:
        st.error(exc.message)
        return
    initialize_session_state(st.session_state)

    with st.sidebar:
        st.title("Task Manager")
        page = st.radio("Navigate", ["Chat", "Tasks"], label_visibility="collapsed")

    if page == "Chat":
        _render_chat(client, st.session_state)
    else:
        _render_tasks(client, st.session_state)


if __name__ == "__main__":
    main()
