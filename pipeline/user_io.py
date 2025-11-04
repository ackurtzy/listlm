"""Terminal input and output helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.models import SearchPlan, UserRequest


class TerminalIO:
    """Handles terminal-based user input and output."""

    def collect_user_request(self) -> UserRequest:
        """Collects the base user request from stdin."""
        description = ""
        while not description:
            description = input(
                "Describe what you are looking for (type 'help' for guidance): "
            ).strip()
            if self._is_help(description):
                self._print_description_help()
                description = ""
                continue

        min_items = self._prompt_for_int(
            "Minimum number of items (type 'help' for guidance): ",
            self._print_min_items_help,
        )
        if min_items > 100:
            print("Minimum number capped at 100; using 100.")
            min_items = 100

        while True:
            columns_raw = input(
                "Optional: comma-separated column names for the CSV (blank to skip, 'help' for guidance): "
            ).strip()
            if self._is_help(columns_raw):
                self._print_columns_help()
                continue
            break

        columns = (
            [col.strip() for col in columns_raw.split(",") if col.strip()]
            if columns_raw
            else None
        )
        dedupe_field = self._prompt_dedupe_column(columns)
        return UserRequest(
            description=description,
            min_items=min_items,
            columns=columns,
            dedupe_field=dedupe_field,
        )

    def review_search_plan(self, plan: SearchPlan) -> Dict[str, Any]:
        """Allows the user to approve or edit the search plan."""
        drop_ids: List[str] = []
        new_queries: List[str] = []
        feedback: Optional[str] = None

        while True:
            self._display_plan(plan)
            print(
                "Options: [A]pprove plan, [D]rop IDs, [N]ew search, "
                "[F]eedback, [G] Re-filter with feedback, [R]efresh, [H]elp"
            )
            choice = input("Select an option: ").strip().lower()
            if choice in {"a", "approve"}:
                return {
                    "approved": True,
                    "drop_ids": drop_ids,
                    "new_queries": new_queries,
                    "feedback": feedback,
                    "regenerate": False,
                    "refilter": False,
                }
            if choice in {"d", "drop"}:
                ids_raw = input("Enter IDs to drop (comma-separated): ").strip()
                ids = [item.strip() for item in ids_raw.split(",") if item.strip()]
                drop_ids.extend(ids)
                plan.remove_ids(ids)
            elif choice in {"n", "new", "add"}:
                new_query = input("Enter the new search query: ").strip()
                if new_query:
                    new_queries.append(new_query)
                    print(f"Added new search query: {new_query}")
            if choice in {"h", "help", "?"}:
                self._print_review_help()
            elif choice in {"f", "feedback"}:
                feedback_text = input("Enter feedback for the system: ").strip()
                if feedback_text:
                    return {
                        "approved": False,
                        "drop_ids": drop_ids,
                        "new_queries": new_queries,
                        "feedback": feedback_text,
                        "regenerate": True,
                        "refilter": False,
                    }
            elif choice in {"g", "refilter"}:
                filter_feedback = input(
                    "Enter feedback specifically for filtering: "
                ).strip()
                return {
                    "approved": False,
                    "drop_ids": drop_ids,
                    "new_queries": new_queries,
                    "feedback": feedback,
                    "regenerate": False,
                    "refilter": True,
                    "filter_feedback": filter_feedback or "",
                }
            else:
                continue

    def display_status(self, message: str) -> None:
        """Prints a status message."""
        print(f"[status] {message}")

    def _display_plan(self, plan: SearchPlan) -> None:
        """Prints the current search plan."""
        print("\nCurrent search plan:")
        for task in plan.tasks:
            rationale = f" ({task.rationale})" if task.rationale else ""
            print(f"  [{task.id}] {task.strategy}: {task.query}{rationale}")
        print()

    def _prompt_for_int(
        self,
        prompt: str,
        help_callback: Optional[Callable[[], None]] = None,
    ) -> int:
        """Prompts until a valid integer is provided."""
        while True:
            raw = input(prompt).strip()
            if self._is_help(raw):
                if help_callback:
                    help_callback()
                else:
                    self._print_min_items_help()
                continue
            try:
                value = int(raw)
                if value > 0:
                    return value
                print("Please enter a positive integer.")
            except ValueError:
                print("Invalid integer, please try again.")

    @staticmethod
    def _is_help(value: str) -> bool:
        """Returns True if the input represents a help request."""
        return value.lower() in {"help", "h", "?"}

    @staticmethod
    def _print_description_help() -> None:
        """Shows guidance for the description prompt."""
        print(
            "\nEnter a concise description of the items you want the system to find.\n"
            "Example: 'Boston-area robotics companies focused on warehouse automation'.\n"
            "The description guides all downstream search prompts.\n"
        )

    @staticmethod
    def _print_min_items_help() -> None:
        """Shows guidance for the minimum items prompt."""
        print(
            "\nProvide the minimum number of unique items you need in the final CSV "
            "(maximum of 100).\n"
            "The system keeps searching and refining until at least this many polished\n"
            "results are produced (or retries are exhausted).\n"
        )

    @staticmethod
    def _print_columns_help() -> None:
        """Shows guidance for the optional column list prompt."""
        print(
            "\nOptional: specify custom column headers separated by commas.\n"
            "Example: 'name,website,description,email'. Leave blank to let the system\n"
            "infer an appropriate schema automatically.\n"
        )

    @staticmethod
    def _print_review_help() -> None:
        """Shows guidance for the search-plan review menu."""
        print(
            "\n[A]pprove: accept the current plan and run the searches.\n"
            "[D]rop: remove one or more tasks by ID (comma-separated).\n"
            "[N]ew: add an extra search query; the system assigns a new ID and uses\n"
            "       the 'web' strategy.\n"
            "[F]eedback: share guidance for future retries (e.g., 'prioritize startups').\n"
            "[G] Re-filter: add feedback for the filter step and rebuild the plan immediately.\n"
            "[R]efresh: redisplay the current plan without making changes.\n"
            "[H]elp: show this menu again.\n"
        )

    def _prompt_dedupe_column(self, columns: Optional[List[str]]) -> Optional[str]:
        """Prompts the user to select a column for duplicate removal."""
        available = columns or []
        candidates = {
            "name": "name",
            "website": "website",
            "link": "link",
            "url": "url",
            "email": "email",
            "description": "description",
        }
        picked = None
        while picked is None:
            prompt = (
                "Dedupe column (name/website/link/url/email/description, blank for name): "
            )
            choice = input(prompt).strip().lower()
            if not choice:
                return "name"
            if choice in candidates:
                if available and choice not in available and choice not in {"name", "description"}:
                    print(f"Column '{choice}' is not in the schema; please choose another.")
                    continue
                picked = candidates[choice]
            else:
                print("Unknown column. Allowed options: name, website, link, url, email, description.")
        return picked
