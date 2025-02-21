import gi
import webbrowser
import requests
import json
import re
import concurrent.futures

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

class SearchBar(Gtk.Window):
    def __init__(self):
        super().__init__(title="Search")
        self.set_default_size(700, 20)
        self.apply_css()

        # Close the window when it loses focus.
        self.connect("focus-out-event", self.on_focus_out)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # Search Entry
        self.entry = Gtk.Entry()
        self.set_decorated(False)
        self.entry.set_placeholder_text("Type to search...")
        self.entry.set_name("search_entry")
        self.entry.connect("changed", self.on_entry_changed)
        self.entry.connect("activate", self.search_google)
        vbox.pack_start(self.entry, False, False, 0)
        
        self.set_position(Gtk.WindowPosition.CENTER)

        # Suggestion List
        self.listbox = Gtk.ListBox()
        self.listbox.set_name("suggestion_list")
        self.listbox.connect("row-activated", self.select_suggestion)
        vbox.pack_start(self.listbox, True, True, 0)

        self.suggestions = []
        self.selected_index = -1
        self.navigating = False

        self.debounce_id = None
        self.cache = {}      # Cache suggestions by query
        self.current_query = ""  # Latest query text

        self.session = requests.Session()  # HTTP connection reuse
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        self.connect("key-press-event", self.on_key_press)

    def on_focus_out(self, widget, event):
        """Close the search bar if it loses focus."""
        self.close()
        return False

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True

        if not self.suggestions:
            return False

        if event.keyval in [Gdk.KEY_Up, Gdk.KEY_Down]:
            if event.keyval == Gdk.KEY_Up and self.selected_index > 0:
                self.selected_index -= 1
            elif event.keyval == Gdk.KEY_Down and self.selected_index < len(self.suggestions) - 1:
                self.selected_index += 1
            self.navigating = True
            self.update_entry_from_suggestion(append=True)
            return True

        if event.keyval == Gdk.KEY_Tab:
            shift_pressed = event.state & Gdk.ModifierType.SHIFT_MASK
            if shift_pressed:
                if self.selected_index > 0:
                    self.selected_index -= 1
            else:
                if self.selected_index < len(self.suggestions) - 1:
                    self.selected_index += 1
            self.navigating = True
            self.update_entry_from_suggestion(append=False)
            return True

        return False

    def update_entry_from_suggestion(self, append=False):
        if 0 <= self.selected_index < len(self.suggestions):
            suggestion = self.suggestions[self.selected_index]
            current_text = self.entry.get_text()
            if append:
                suffix = current_text[len(suggestion):] if current_text.startswith(suggestion) else ""
                self.entry.set_text(suggestion + suffix)
            else:
                self.entry.set_text(suggestion)
        self.entry.set_position(-1)

    def apply_css(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
            #search_entry {
                font-size: 18px;
                padding: 10px;
                background-color: #2E2E2E;
                color: white;
                border: 2px solid #4A90E2;
            }
            #search_entry:focus {
                border: 2px solid #76A9FA;
                background-color: #383838;
            }
            #suggestion_list {
                background-color: #2E2E2E;
            }
            row {
                padding: 8px;
                font-size: 16px;
                color: white;
                background-color: transparent;
                border-radius: 6px;
            }
            row:hover, row:selected {
                background-color: #4A90E2;
            }
            """
        )
        screen = Gdk.Screen.get_default()
        context = Gtk.StyleContext()
        context.add_provider_for_screen(screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def on_entry_changed(self, widget):
        if self.debounce_id is not None:
            try:
                GLib.source_remove(self.debounce_id)
            except Exception:
                pass
        self.debounce_id = GLib.timeout_add(100, self.debounce_get_suggestions)

    def debounce_get_suggestions(self):
        query = self.entry.get_text().strip()
        self.current_query = query
        if query:
            if query in self.cache:
                GLib.idle_add(self.update_suggestions, self.cache[query])
                return False
            # Check for prefix cache hit
            prefix_key = None
            for k in self.cache:
                if query.startswith(k):
                    if prefix_key is None or len(k) > len(prefix_key):
                        prefix_key = k
            if prefix_key:
                GLib.idle_add(self.update_suggestions, self.cache[prefix_key])
                return False
            self.executor.submit(self.get_suggestions, query)
        else:
            # When search box is empty, clear suggestions and reset window size.
            GLib.idle_add(self.clear_suggestions)
            GLib.idle_add(self.reset_window_size)
        self.debounce_id = None
        return False

    def get_suggestions(self, query):
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={query}"
        try:
            response = self.session.get(url, timeout=0.5)
            suggestions = json.loads(response.text)[1]
        except Exception:
            suggestions = []
        self.cache[query] = suggestions
        if query == self.current_query:
            GLib.idle_add(self.update_suggestions, suggestions)

    def update_suggestions(self, suggestions):
        self.clear_suggestions()
        self.suggestions = [s for s in suggestions if s != self.entry.get_text().strip()]
        self.selected_index = -1
        for suggestion in self.suggestions:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=suggestion)
            label.set_xalign(0)
            row.add(label)
            row.show_all()
            self.listbox.add(row)

    def clear_suggestions(self):
        for row in self.listbox.get_children():
            self.listbox.remove(row)

    def reset_window_size(self):
        """Reset window size to the initial dimensions (700×20)."""
        self.resize(700, 20)

    def select_suggestion(self, listbox, row):
        text = row.get_child().get_text()
        self.entry.set_text(text)
        self.search_google(None)

    def search_google(self, widget):
        query = self.entry.get_text().strip()
        if not query:
            return

        if "/" in query:
            site, search_term = query.split("/", 1)
            site_search_map = {
                "youtube": f"https://www.youtube.com/results?search_query={search_term}",
                "youtube.com": f"https://www.youtube.com/results?search_query={search_term}",
                "google": f"https://www.google.com/search?q={search_term}",
                "github": f"https://github.com/search?q={search_term}",
                "reddit": f"https://www.reddit.com/search/?q={search_term}",
                "amazon": f"https://www.amazon.com/s?k={search_term}",
                "flipkart": f"https://www.flipkart.com/search?q={search_term}",
                "stackoverflow": f"https://stackoverflow.com/search?q={search_term}",
                "wikipedia": f"https://en.wikipedia.org/wiki/Special:Search?search={search_term}",
            }
            url = site_search_map.get(site, f"https://www.google.com/search?q={query}")
        elif re.match(r"^(https?:\/\/)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})$", query):
            url = query if query.startswith(("http://", "https://")) else f"http://{query}"
        else:
            url = f"https://www.google.com/search?q={query}"
        webbrowser.open_new_tab(url)
        self.close()

win = SearchBar()
win.connect("destroy", Gtk.main_quit)
win.show_all()
Gtk.main()
