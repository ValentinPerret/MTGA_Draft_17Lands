import pytest
import tkinter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from src.ui.card_interactions import CardInteractionManager
from src.constants import DATA_FIELD_NAME


class TestCardInteractions:
    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.root = tkinter.Tk()
        app.current_pack_data = [{DATA_FIELD_NAME: "Lightning Bolt", "cmc": 1}]
        app.current_missing_data = [{DATA_FIELD_NAME: "Giant Growth", "cmc": 1}]
        app.configuration.settings.ui_size = "100%"
        app.configuration.features.images_enabled = False
        yield app
        app.root.destroy()

    @patch("src.ui.card_interactions.CardToolTip.create")
    def test_on_card_select_pack(self, mock_tooltip, mock_app):
        manager = CardInteractionManager(mock_app)

        mock_table = MagicMock()
        mock_table.identify_region.return_value = "cell"
        mock_table.identify_row.return_value = "item1"
        mock_table.selection.return_value = ["item1"]
        mock_table.item.return_value = {"text": "Lightning Bolt"}

        # Simulate click
        class MockEvent:
            x = 10
            y = 10

        manager.on_card_select(MockEvent(), mock_table, "pack")

        # Tooltip should trigger with the right card data
        mock_tooltip.assert_called_once()
        mock_table.selection_set.assert_called_once_with("item1")
        assert mock_tooltip.call_args[0][1][DATA_FIELD_NAME] == "Lightning Bolt"

    @patch("src.ui.card_interactions.CardToolTip.create")
    def test_on_card_select_missing(self, mock_tooltip, mock_app):
        manager = CardInteractionManager(mock_app)

        mock_table = MagicMock()
        mock_table.identify_region.return_value = "cell"
        mock_table.identify_row.return_value = "item1"
        mock_table.selection.return_value = ["item1"]
        mock_table.item.return_value = {"text": "Giant Growth"}

        manager.on_card_select(MagicMock(x=10, y=10), mock_table, "missing")

        mock_tooltip.assert_called_once()
        mock_table.selection_set.assert_called_once_with("item1")
        assert mock_tooltip.call_args[0][1][DATA_FIELD_NAME] == "Giant Growth"

    def test_context_menu_spawns(self, mock_app):
        manager = CardInteractionManager(mock_app)

        mock_table = MagicMock()
        mock_table.identify_region.return_value = "cell"
        mock_table.identify_row.return_value = "item1"
        mock_table.item.return_value = {"text": "Lightning Bolt"}

        with patch("tkinter.Menu.post") as mock_post:
            manager.on_card_context_menu(
                MagicMock(x=10, y=10, x_root=100, y_root=100), mock_table, "pack"
            )
            mock_post.assert_called_once_with(100, 100)

    @patch("src.ui.card_interactions.open_file")
    def test_open_scryfall(self, mock_open_file, mock_app):
        manager = CardInteractionManager(mock_app)
        manager.open_scryfall("Light up the Stage")

        mock_open_file.assert_called_once_with(
            "https://scryfall.com/search?q=Light%20up%20the%20Stage"
        )

    @patch("src.ui.card_interactions.CardToolTip.create")
    def test_delayed_hover_previews_card_without_pinning(
        self, mock_tooltip, mock_app
    ):
        manager = CardInteractionManager(mock_app)
        manager.HOVER_DELAY_MS = 1
        callbacks = {}
        table = MagicMock()
        table._card_hover_bound = False
        table.bind.side_effect = lambda sequence, callback, add=None: callbacks.update(
            {sequence: callback}
        )
        table.after.side_effect = mock_app.root.after
        table.after_cancel.side_effect = mock_app.root.after_cancel
        table.identify_region.return_value = "cell"
        table.identify_row.return_value = "row-1"
        table.winfo_exists.return_value = True
        table.item.return_value = {"text": "Lightning Bolt"}

        manager.bind_hover_preview(table, lambda: mock_app.current_pack_data)
        callbacks["<Motion>"](SimpleNamespace(x=2, y=10))
        mock_app.root.after(10, mock_app.root.quit)
        mock_app.root.mainloop()

        mock_tooltip.assert_called_once()
        assert mock_tooltip.call_args.kwargs["persistent"] is False

    @patch("src.ui.card_interactions.CardToolTip.create")
    def test_leaving_table_cancels_delayed_hover(self, mock_tooltip, mock_app):
        manager = CardInteractionManager(mock_app)
        manager.HOVER_DELAY_MS = 5
        callbacks = {}
        table = MagicMock()
        table._card_hover_bound = False
        table.bind.side_effect = lambda sequence, callback, add=None: callbacks.update(
            {sequence: callback}
        )
        table.after.side_effect = mock_app.root.after
        table.after_cancel.side_effect = mock_app.root.after_cancel
        table.identify_region.return_value = "cell"
        table.identify_row.return_value = "row-1"
        table.winfo_exists.return_value = True
        table.item.return_value = {"text": "Lightning Bolt"}

        manager.bind_hover_preview(table, lambda: mock_app.current_pack_data)
        callbacks["<Motion>"](SimpleNamespace(x=2, y=10))
        callbacks["<Leave>"]()
        mock_app.root.after(15, mock_app.root.quit)
        mock_app.root.mainloop()

        mock_tooltip.assert_not_called()
