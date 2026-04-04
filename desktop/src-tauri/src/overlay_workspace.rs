//! Place the overlay on the user's current Space / virtual desktop / workspace when shown.

use tauri::{Manager, Runtime, WebviewWindow};

/// Align the overlay with the active desktop, then show it.
///
/// On **macOS**, after [`crate::macos_overlay_panel::init_overlay_panel`], the overlay is an
/// **`NSPanel`**. Use [`tauri_nspanel::Panel::show`] (implemented as `orderFrontRegardless`), not
/// `WebviewWindow::show` (`makeKeyAndOrderFront`), or you break **`NonactivatingPanel`** and
/// full-screen behavior (see [tauri-macos-spotlight-example](https://github.com/ahkohd/tauri-macos-spotlight-example)).
///
/// `set_focus` still maps to `activateIgnoringOtherApps:` on macOS — we never call it for the overlay.
pub(crate) fn show_overlay_window<R: Runtime>(w: &WebviewWindow<R>) {
    overlay_show_on_active_workspace(w);
    #[cfg(target_os = "macos")]
    {
        use tauri_nspanel::ManagerExt;
        match w.app_handle().get_webview_panel(w.label()) {
            Ok(panel) => {
                tauri_nspanel::Panel::show(&*panel);
            }
            Err(_) => {
                let _ = w.show();
            }
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

/// Before `show()` + `set_focus()`, align the window with the active desktop (Spaces / VD / WM).
pub(crate) fn overlay_show_on_active_workspace<R: Runtime>(w: &WebviewWindow<R>) {
    #[cfg(target_os = "macos")]
    {
        // Full-screen / Spaces behavior is configured once on `NSPanel` (`macos_overlay_panel`).
        macos_round_overlay_window(w);
    }

    #[cfg(windows)]
    windows_move_to_current_desktop(w);

    #[cfg(any(
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    ))]
    linux_gtk_present(w);
}

/// Match `border-radius` on `.overlay__bar` in `App.css` so the borderless window clips to the same
/// shape (CSS only rounds the web layer; the NSWindow frame stays square without this).
#[cfg(target_os = "macos")]
fn macos_round_overlay_window<R: Runtime>(w: &WebviewWindow<R>) {
    const RADIUS_PX: f64 = 10.0;
    let Ok(ptr) = w.ns_window() else {
        return;
    };
    unsafe {
        use objc2_app_kit::{NSView, NSWindow};
        use objc2_core_foundation::CGFloat;
        let ns_window = &*ptr.cast::<NSWindow>();
        let Some(content) = ns_window.contentView() else {
            return;
        };
        let content: &NSView = &*content;
        content.setWantsLayer(true);
        if let Some(layer) = content.layer() {
            layer.setCornerRadius(RADIUS_PX as CGFloat);
            layer.setMasksToBounds(true);
        }
    }
}

#[cfg(windows)]
fn windows_move_to_current_desktop<R: Runtime>(w: &WebviewWindow<R>) {
    use windows::Win32::Foundation::HWND;

    let Ok(hwnd_tauri) = w.hwnd() else {
        return;
    };
    let Ok(desktop) = winvd::get_current_desktop() else {
        return;
    };
    // Tauri uses `windows` 0.61 (`HWND` wraps a pointer); `winvd` uses 0.44 (`HWND` wraps `isize`).
    let hwnd = HWND(hwnd_tauri.0 as isize);
    let _ = winvd::move_window_to_desktop(desktop, &hwnd);
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_gtk_present<R: Runtime>(w: &WebviewWindow<R>) {
    use gtk::prelude::GtkWindowExt;

    if let Ok(gw) = w.gtk_window() {
        gw.present();
    }
}
