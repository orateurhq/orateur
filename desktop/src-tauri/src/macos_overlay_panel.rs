//! Convert the overlay `NSWindow` to an `NSPanel` so it can participate in full-screen Spaces.
//!
//! Standard `NSWindow` is limited above native full-screen apps; see
//! <https://github.com/tauri-apps/tauri/issues/11488> and [tauri-nspanel](https://github.com/ahkohd/tauri-nspanel).

use tauri::ActivationPolicy;
use tauri::{AppHandle, Manager, Wry};
use tauri_nspanel::{CollectionBehavior, PanelLevel, StyleMask, WebviewWindowExt};

tauri_nspanel::tauri_panel! {
    OrateurOverlayPanel {
        config: {
            can_become_key_window: true,
            is_floating_panel: true,
            become_key_if_only_needed: true,
        }
    }
}

/// Subclass the overlay webview window as `NSPanel`, set full-screen–friendly behaviors, then
/// restore regular activation policy (Dock icon) like the EcoPaste workaround in tauri#11488.
///
/// Matches [tauri-macos-spotlight-example](https://github.com/ahkohd/tauri-macos-spotlight-example)
/// (`window.rs`): `move_to_active_space` + `full_screen_auxiliary`, **`NonactivatingPanel`** style,
/// not `CanJoinAllSpaces` + `Stationary` (those fight full-screen Spaces). After this, show the
/// overlay via [`tauri_nspanel::Panel::show`] (orderFrontRegardless), not `WebviewWindow::show`
/// (makeKeyAndOrderFront), or the panel will not behave correctly.
pub fn init_overlay_panel(app: &AppHandle<Wry>) -> Result<(), String> {
    let window = app
        .get_webview_window("overlay")
        .ok_or_else(|| "overlay window missing".to_string())?;

    app.set_activation_policy(ActivationPolicy::Accessory)
        .map_err(|e| e.to_string())?;

    let panel = window
        .to_panel::<OrateurOverlayPanel<Wry>>()
        .map_err(|e| e.to_string())?;

    panel.set_collection_behavior(
        CollectionBehavior::new()
            .move_to_active_space()
            .full_screen_auxiliary()
            .value(),
    );
    // Spotlight example uses Floating; Status helps stack above native full-screen tiles.
    panel.set_level(PanelLevel::Status.value());
    panel.set_style_mask(
        StyleMask::empty()
            .borderless()
            .nonactivating_panel()
            .value(),
    );

    app.set_activation_policy(ActivationPolicy::Regular)
        .map_err(|e| e.to_string())?;

    let _ = window.hide();
    Ok(())
}
