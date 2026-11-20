using System.Drawing;

namespace MaaShinyColors.BrowserHost;

/// <summary>One docked layout: the display scale and the client rectangles of every host part.</summary>
/// <remarks><c>ButtonsScale</c>: zoom of the window buttons (1 over the panel, which never zooms; the game scale when the
/// panel is on the left and the buttons sit over the game header).</remarks>
internal readonly record struct DockLayout(double Scale, Rectangle Header, Rectangle Game, Rectangle Footer, Rectangle Panel, Rectangle Buttons,
    double ButtonsScale = 1);

/// <summary>
/// Display-only scaling of the docked game host. The game WebView always renders, is
/// captured and receives program input at exactly 1280x720 (HostGuard.Canvas, CoreWebView2 Bounds unchanged); only the
/// DirectComposition root visual is scaled for display, and the viewport HWND is sized to the scaled picture.
/// layout: the game column (header strip, game, footer) scales to the window height; the panel takes all the
/// remaining width and height. 2026-10-25 (user): the panel page no longer zooms with the window: its text follows
/// only the system DPI and 设置 → 外观 → 字号, and a wider panel reflows (双栏 layout) instead. The game is only held
/// below the height scale when the panel would otherwise get narrower than its minimum width (a fixed physical width).
/// Pure math, no window: <see cref="SelfCheck"/> runs offline.
/// </summary>
internal static class GameDisplay
{
    internal const double MinScale = 0.6;
    internal const double MaxScale = 3.0;
    internal const int HeaderHeight = 44, FooterHeight = 24;
    internal const int BaseHeight = HeaderHeight + BrowserHostOptions.ViewportHeight + FooterHeight;   // 788
    /// <summary>Narrowest panel page (CSS px at zoom 1, 96 dpi); the panel always gets at least this × DPI factor.</summary>
    internal const int MinPanelCss = 360;
    private static Size Canvas => HostGuard.Canvas;

    /// <summary>
    /// 2026-10-15 (150 % display): the header and footer strips hold DPI-scaled text and icons, so their height is
    /// <c>base × chrome × scale</c> with <paramref name="chrome"/> = window DPI / 96. Only the 1280x720 canvas ignores the
    /// DPI (its display size is canvas × scale). At 96 dpi (chrome 1) every result is unchanged.
    /// </summary>
    internal static double BaseHeightFor(double chrome) => (HeaderHeight + FooterHeight) * chrome + BrowserHostOptions.ViewportHeight;

    internal static int HeaderPx(double scale, double chrome = 1) => (int)Math.Round(HeaderHeight * chrome * scale);
    internal static int FooterPx(double scale, double chrome = 1) => (int)Math.Round(FooterHeight * chrome * scale);

    /// <summary>
    /// Scale for a client size: the height scale (column height / 788 at 96 dpi), held down so that the game plus a
    /// minimum-width panel fit the width (unclamped). <paramref name="minPanelPx"/> is the minimum panel width in physical
    /// px; it does not scale with the game.
    /// </summary>
    internal static double FitScale(Size client, int minPanelPx, double chrome = 1) =>
        Math.Min(client.Height / BaseHeightFor(chrome), (double)(client.Width - minPanelPx) / Canvas.Width);

    /// <summary>Clamps to [MinScale, MaxScale] and snaps values within 0.2 % of 1 to exactly 1 (1:1 pixels, no resampling).</summary>
    internal static double Normalize(double scale)
    {
        if (double.IsNaN(scale)) { return 1; }
        scale = Math.Clamp(scale, MinScale, MaxScale);
        return Math.Abs(scale - 1) < 0.002 ? 1 : scale;
    }

    /// <summary>Displayed size of the 1280x720 canvas at a scale.</summary>
    internal static Size DisplaySize(double scale) =>
        new((int)Math.Round(Canvas.Width * scale), (int)Math.Round(Canvas.Height * scale));

    /// <summary>Height of the game column (header + game + footer) at a scale.</summary>
    internal static int ColumnHeight(double scale, double chrome = 1) =>
        HeaderPx(scale, chrome) + DisplaySize(scale).Height + FooterPx(scale, chrome);

    /// <summary>
    /// A displayed game size the guard accepts: inside the scale range and within one pixel of 16:9 (the rounding of
    /// <see cref="DisplaySize"/>). Anything else means the viewport and the committed visual transform disagree.
    /// </summary>
    internal static bool DisplayAllowed(Size display) =>
        display.Width >= (int)Math.Floor(Canvas.Width * MinScale) && display.Width <= (int)Math.Ceiling(Canvas.Width * MaxScale)
        && Math.Abs((long)display.Height * Canvas.Width - (long)display.Width * Canvas.Height) <= Canvas.Width;

    /// <summary>
    /// Smallest client size (physical px) that shows the game at exactly <paramref name="scale"/> with a panel of
    /// <paramref name="panelPx"/> (unscaled; the resize border lies inside it and only trims the panel).
    /// </summary>
    internal static Size ClientFor(double scale, int panelPx, double chrome = 1)
    {
        scale = Normalize(scale);
        return new(DisplaySize(scale).Width + panelPx, ColumnHeight(scale, chrome));
    }

    /// <summary>
    /// Layout for any client size. The game column sits at the top left at <see cref="FitScale"/>; the panel fills the
    /// rest of the width and the full height, trimmed by the resize <paramref name="border"/> at the top, right and bottom
    /// (so those window edges stay grab-able); the window buttons sit at the right end of the header row.
    /// <paramref name="scaling"/> false (the visual transform is unavailable) keeps the game at 1:1.
    /// </summary>
    /// <paramref name="panelLeft"/> (设置 → 外观 面板位置, 2026-10-16) mirrors it: the game column sits at the top right, the
    /// panel fills the left part trimmed by the border at the left, top and bottom; the window buttons stay at the right end
    /// of the header row (now over the game header, which keeps that end free).
    /// <paramref name="detached"/> (面板位置「独立窗口」): the panel lives in its own window, so this window holds only
    /// the game column, centred horizontally when the window is wider; the panel rectangle is empty and the window buttons
    /// sit at the right end of the game header.
    internal static DockLayout Compute(Size client, int border, int minPanelPx, int buttonsPx, bool scaling, double chrome = 1, bool panelLeft = false,
        bool detached = false)
    {
        var scale = scaling ? Normalize(FitScale(client, detached ? 0 : minPanelPx, chrome)) : 1;
        var game = DisplaySize(scale);
        var header = HeaderPx(scale, chrome);
        var footer = FooterPx(scale, chrome);
        var x = detached ? Math.Max(0, (client.Width - game.Width) / 2) : panelLeft ? Math.Max(0, client.Width - game.Width) : 0;
        var bottom = Math.Max(border, client.Height - border);
        var panel = detached ? Rectangle.Empty
            : panelLeft ? Rectangle.FromLTRB(Math.Min(border, x), border, x, bottom)
            : Rectangle.FromLTRB(game.Width, border, Math.Max(game.Width, client.Width - border), bottom);
        // buttonsPx is already DPI-scaled (UiWindowButtons.ScaledWidth). Over the panel (right) they keep the panel's
        // unzoomed size and header height; over the game header (panel left / detached) they scale with the game.
        var overGame = panelLeft || detached;
        var buttonsScale = overGame ? scale : 1;
        var buttons = overGame
            ? Math.Min((int)Math.Round(buttonsPx * scale), game.Width)
            : Math.Min(buttonsPx, Math.Max(0, client.Width - game.Width));
        var buttonsHeight = overGame ? header : HeaderPx(1, chrome);
        var right = detached ? x + game.Width : client.Width;
        return new(scale, new Rectangle(x, 0, game.Width, header), new Rectangle(new Point(x, header), game),
            new Rectangle(x, header + game.Height, game.Width, footer), panel,
            new Rectangle(right - buttons, 0, buttons, buttonsHeight), buttonsScale);
    }

    /// <summary>
    /// User drag of the game-only window (detached panel): the window keeps the game column's shape. A side drag sets
    /// the scale from the width, a top / bottom drag from the height, a corner drag from the larger of the two; the edge
    /// opposite the dragged one stays put. Always at least <paramref name="minimum"/>.
    /// </summary>
    internal static Rectangle DragSizingDetached(Rectangle window, int edge, Size minimum, double chrome = 1)
    {
        var byWidth = Normalize(Math.Max(window.Width, minimum.Width) / (double)Canvas.Width);
        var byHeight = Normalize(Math.Max(window.Height, minimum.Height) / BaseHeightFor(chrome));
        var scale = edge is EdgeLeft or EdgeRight ? byWidth : edge is EdgeTop or EdgeBottom ? byHeight : Math.Max(byWidth, byHeight);
        var size = ClientFor(scale, 0, chrome);
        size = new Size(Math.Max(size.Width, minimum.Width), Math.Max(size.Height, minimum.Height));
        var left = edge is EdgeLeft or EdgeTopLeft or EdgeBottomLeft ? window.Right - size.Width : window.Left;
        var top = edge is EdgeTop or EdgeTopLeft or EdgeTopRight ? window.Bottom - size.Height : window.Top;
        return new Rectangle(left, top, size.Width, size.Height);
    }

    // WM_SIZING edges (winuser.h WMSZ_*).
    internal const int EdgeLeft = 1, EdgeRight = 2, EdgeTop = 3, EdgeTopLeft = 4, EdgeTopRight = 5, EdgeBottom = 6, EdgeBottomLeft = 7, EdgeBottomRight = 8;

    /// <summary>
    /// User drag (WM_SIZING): width-only drags are free (the panel absorbs them). When the height changes (top / bottom
    /// edges and corners) and the window is too narrow for the game at that height plus a minimum panel, the window is
    /// widened (up to <paramref name="maxWidth"/>), and if that is still not enough the height is reduced, so a drag
    /// never leaves an empty band under the game. Always at least <paramref name="minimum"/>.
    /// </summary>
    internal static Rectangle DragSizing(Rectangle window, int edge, int minPanelPx, int maxWidth, Size minimum, double chrome = 1)
    {
        var width = Math.Max(window.Width, minimum.Width);
        var height = Math.Max(window.Height, minimum.Height);
        var vertical = edge is EdgeTop or EdgeBottom or EdgeTopLeft or EdgeTopRight or EdgeBottomLeft or EdgeBottomRight;
        if (vertical)
        {
            var scale = Normalize(height / BaseHeightFor(chrome));
            var need = ClientFor(scale, minPanelPx, chrome).Width;
            if (width < need) { width = Math.Max(width, Math.Min(need, Math.Max(maxWidth, minimum.Width))); }
            if (width < need)
            {
                var fitted = Normalize((double)(width - minPanelPx) / Canvas.Width);
                height = Math.Max(minimum.Height, Math.Min(height, ColumnHeight(fitted, chrome)));
            }
        }
        var left = edge is EdgeLeft or EdgeTopLeft or EdgeBottomLeft ? window.Right - width : window.Left;
        // A pure top / bottom drag that had to widen the window grows to the right.
        var top = edge is EdgeTop or EdgeTopLeft or EdgeTopRight ? window.Bottom - height : window.Top;
        return new Rectangle(left, top, width, height);
    }

    /// <summary>
    /// Manual mouse: a viewport (display) point to the canvas point the WebView expects (pixel-centre mapping through the
    /// inverse of the display transform), clamped to the canvas. Program input never goes through here: it is already
    /// in canvas coordinates.
    /// </summary>
    internal static Point ToCanvas(Point display, Size displaySize) => new(
        Math.Clamp((int)Math.Floor((display.X + 0.5) * Canvas.Width / displaySize.Width), 0, Canvas.Width - 1),
        Math.Clamp((int)Math.Floor((display.Y + 0.5) * Canvas.Height / displaySize.Height), 0, Canvas.Height - 1));

    /// <summary>The display pixel whose centre shows a canvas pixel's centre (self-tests and diagnostics).</summary>
    internal static Point ToDisplay(Point canvas, Size displaySize) => new(
        Math.Clamp((int)Math.Floor((canvas.X + 0.5) * displaySize.Width / Canvas.Width), 0, displaySize.Width - 1),
        Math.Clamp((int)Math.Floor((canvas.Y + 0.5) * displaySize.Height / Canvas.Height), 0, displaySize.Height - 1));

    /// <summary>Offline truth table; never creates a window or browser.</summary>
    internal static void SelfCheck(Action<bool> check)
    {
        const int MinPanel = MinPanelCss, Buttons = 138, Border = 5;
        // Default 1680x788 window: game 1:1 at 0,44, the panel fills x>=1280 (400 px minus the border).
        var unit = Compute(new Size(1680, 788), Border, MinPanel, Buttons, scaling: true);
        check(unit.Scale == 1 && unit.Game == new Rectangle(0, 44, 1280, 720) && unit.Header == new Rectangle(0, 0, 1280, 44)
            && unit.Footer == new Rectangle(0, 764, 1280, 24) && unit.Panel == Rectangle.FromLTRB(1280, Border, 1680 - Border, 788 - Border)
            && unit.Buttons == new Rectangle(1680 - 138, 0, 138, 44));
        // Wider window: the game stays at the height scale, the panel gets all the extra width.
        var wider = Compute(new Size(2300, 788), Border, MinPanel, Buttons, true);
        check(wider.Scale == 1 && wider.Game.Size == new Size(1280, 720) && wider.Panel.Width == 2300 - Border - 1280 && wider.Buttons.Right == 2300);
        // Taller window: the game grows with the height while the panel keeps at least its minimum width. The window
        // buttons over the panel keep their unscaled size (the panel page does not zoom).
        var taller = Compute(new Size(2100, 985), Border, MinPanel, Buttons, true);
        check(Math.Abs(taller.Scale - 1.25) < 1e-9 && taller.Game.Size == new Size(1600, 900) && taller.Panel.Left == 1600 && taller.Panel.Bottom == 980
            && taller.Footer.Bottom == 985 && taller.Buttons == new Rectangle(2100 - Buttons, 0, Buttons, 44) && taller.ButtonsScale == 1);
        // Maximized on a 1680x1010 work area (16:10): width-limited, the game is as large as a 360 px panel allows (the
        // panel minimum is a fixed width, not × scale) and the panel fills the full height.
        var max1680 = Compute(new Size(1680, 1010), 0, MinPanel, Buttons, true);
        check(Math.Abs(max1680.Scale - 1320.0 / 1280) < 1e-9 && max1680.Game.Width == 1320 && max1680.Panel == Rectangle.FromLTRB(1320, 0, 1680, 1010)
            && max1680.Panel.Width == MinPanel);
        // 1920x1040: height 1040 / 788 = 1.32 needs 2050 px; width-limited to (1920 - 360) / 1280.
        var max1920 = Compute(new Size(1920, 1040), 0, MinPanel, Buttons, true);
        check(max1920.Game.Width == 1560 && max1920.Panel.Right == 1920 && max1920.Panel.Bottom == 1040 && max1920.Panel.Width == MinPanel);
        // Ultra-wide 3440x1400: height-limited, no empty band at all (the panel takes ~1200 px).
        var wide = Compute(new Size(3440, 1400), 0, MinPanel, Buttons, true);
        check(wide.Footer.Bottom == 1400 || Math.Abs(wide.Footer.Bottom - 1400) <= 1);
        foreach (var scale in new[] { 0.6, 0.75, 1.143, 1.25, 1.5, 2.25, 3.0 })
        {
            var client = ClientFor(scale, MinPanel + 100);
            var layout = Compute(client, Border, MinPanel, Buttons, true);
            check(Math.Abs(layout.Scale - scale) < 0.002 && Math.Abs(layout.Game.Width - DisplaySize(scale).Width) <= 1 && DisplayAllowed(layout.Game.Size)
                && layout.Game.Top == layout.Header.Bottom && layout.Footer.Top == layout.Game.Bottom && layout.Panel.Left == layout.Game.Right
                && layout.Buttons.Right == client.Width && !layout.Panel.IntersectsWith(layout.Game) && !layout.Buttons.IntersectsWith(layout.Game));
            var display = DisplaySize(scale);
            // Round trip: exact for every sampled canvas pixel when magnified, within one display pixel's worth when reduced.
            var tolerance = scale >= 1 ? 0 : (int)Math.Ceiling(1 / scale);
            var worst = 0;
            for (var x = 0; x < 1280; x += 7)
            {
                for (var y = 0; y < 720; y += 7)
                {
                    var back = ToCanvas(ToDisplay(new Point(x, y), display), display);
                    worst = Math.Max(worst, Math.Max(Math.Abs(back.X - x), Math.Abs(back.Y - y)));
                }
            }
            check(worst <= tolerance);
            check(ToCanvas(Point.Empty, display) == Point.Empty && ToCanvas(new Point(display.Width - 1, display.Height - 1), display) == new Point(1279, 719)
                && ToCanvas(new Point(-50, 99999), display) == new Point(0, 719));
        }
        check(ToCanvas(new Point(640, 360), new Size(1280, 720)) == new Point(640, 360));
        check(ToCanvas(new Point(800, 450), DisplaySize(1.25)) == new Point(640, 360));
        // A slightly short window still snaps to 1:1 (no resampling blur at the default size).
        check(Compute(new Size(1680, 787), Border, MinPanel, Buttons, true).Game.Size == new Size(1280, 720));
        // Below the minimum scale the layout stays at MinScale (the form also refuses smaller windows).
        check(Compute(new Size(300, 200), Border, MinPanel, Buttons, true).Scale == MinScale);
        // Fallback without the visual transform: game 1:1, the panel takes the rest.
        var fixedLayout = Compute(new Size(2000, 900), Border, MinPanel, Buttons, false);
        check(fixedLayout.Game == new Rectangle(0, 44, 1280, 720) && fixedLayout.Panel == Rectangle.FromLTRB(1280, Border, 2000 - Border, 900 - Border));
        // Guard shapes: 16:9 within a pixel, in range.
        check(DisplayAllowed(new Size(1280, 720)) && DisplayAllowed(new Size(1463, 823)) && DisplayAllowed(new Size(768, 432)));
        check(!DisplayAllowed(new Size(1463, 700)) && !DisplayAllowed(new Size(500, 281)) && !DisplayAllowed(new Size(4000, 2250)) && !DisplayAllowed(Size.Empty));
        // Drag: width-only drags are free; a taller drag widens the window (up to the monitor) or else limits the height.
        var minimum = ClientFor(MinScale, MinPanel);
        var start = new Rectangle(100, 50, 1680, 788);
        check(DragSizing(start with { Width = 2400 }, EdgeRight, MinPanel, 4000, minimum) == start with { Width = 2400 });
        check(DragSizing(Rectangle.FromLTRB(start.Left - 300, start.Top, start.Right, start.Bottom), EdgeLeft, MinPanel, 4000, minimum)
            == Rectangle.FromLTRB(start.Left - 300, start.Top, start.Right, start.Bottom));
        var down = DragSizing(start with { Height = 985 }, EdgeBottom, MinPanel, 4000, minimum);
        check(down.Location == start.Location && down.Height == 985 && down.Width == ClientFor(1.25, MinPanel).Width);
        var capped = DragSizing(start with { Height = 1200 }, EdgeBottom, MinPanel, 1680, minimum);
        check(capped.Width == 1680 && capped.Height == ColumnHeight((1680.0 - MinPanel) / 1280) && capped.Location == start.Location);
        var upLeft = DragSizing(Rectangle.FromLTRB(start.Left, start.Top - 197, start.Right, start.Bottom), EdgeTopLeft, MinPanel, 4000, minimum);
        check(upLeft.Right == start.Right && upLeft.Bottom == start.Bottom && upLeft.Height == 985 && upLeft.Width == ClientFor(1.25, MinPanel).Width);
        check(DragSizing(start with { Width = 200, Height = 100 }, EdgeBottomRight, MinPanel, 4000, minimum).Size == minimum);
        // 2026-10-15, 150 % display (chrome 1.5): header 66 / footer 36 px at scale 1, so the DPI-scaled wordmark, icons
        // and window buttons fit; the canvas itself stays 1280x720 at scale 1.
        const double Hi = 1.5;
        var hiClient = ClientFor(1, (int)Math.Round(MinPanel * Hi) + 60, Hi);
        var hi = Compute(hiClient, Border, (int)Math.Round(MinPanel * Hi), (int)Math.Round(Buttons * Hi), true, Hi);
        check(hiClient.Height == 822 && hi.Scale == 1 && hi.Header == new Rectangle(0, 0, 1280, 66) && hi.Game == new Rectangle(0, 66, 1280, 720)
            && hi.Footer == new Rectangle(0, 786, 1280, 36) && hi.Panel.Left == 1280);
        // 1920x1008 work area at 150 %: width-limited ((1920 - 540) / 1280), the column is header + game + footer exactly.
        var hiMax = Compute(new Size(1920, 1008), 0, 540, 207, true, Hi);
        check(Math.Abs(hiMax.Scale - 1380.0 / 1280) < 1e-9 && hiMax.Header.Height == HeaderPx(hiMax.Scale, Hi) && hiMax.Panel.Width == 540
            && hiMax.Buttons.Height == HeaderPx(1, Hi)
            && hiMax.Game.Top == hiMax.Header.Bottom && hiMax.Footer.Top == hiMax.Game.Bottom && hiMax.Footer.Bottom <= 1008);
        var hiDown = DragSizing(new Rectangle(0, 0, 1880, 822), EdgeBottom, 540, 4000, ClientFor(MinScale, 540, Hi), Hi);
        check(hiDown.Height == 822 && hiDown.Width == 1880);
        // 2026-10-16 面板位置 left: the same sizes mirrored; the game column ends at the right edge, the panel starts after the
        // left border and ends where the game begins, the buttons stay at the right end (over the game header).
        var mirrored = Compute(new Size(1680, 788), Border, MinPanel, Buttons, true, 1, panelLeft: true);
        check(mirrored.Scale == 1 && mirrored.Header == new Rectangle(400, 0, 1280, 44) && mirrored.Game == new Rectangle(400, 44, 1280, 720)
            && mirrored.Footer == new Rectangle(400, 764, 1280, 24) && mirrored.Panel == Rectangle.FromLTRB(Border, Border, 400, 788 - Border)
            && mirrored.Buttons == new Rectangle(1680 - Buttons, 0, Buttons, 44));
        var mirroredWide = Compute(new Size(3440, 1400), 0, MinPanel, Buttons, true, 1, panelLeft: true);
        var wideRight = Compute(new Size(3440, 1400), 0, MinPanel, Buttons, true);
        check(mirroredWide.Scale == wideRight.Scale && mirroredWide.Game.Right == 3440 && mirroredWide.Panel.Left == 0
            && mirroredWide.Panel.Right == mirroredWide.Game.Left && mirroredWide.Panel.Width == wideRight.Panel.Width
            && !mirroredWide.Panel.IntersectsWith(mirroredWide.Game));
        // detached panel: the window holds only the game column (centred when wider), no panel rectangle, the buttons at
        // the right end of the game header; a drag keeps the column's shape.
        var alone = Compute(ClientFor(1, 0), Border, MinPanel, Buttons, true, 1, detached: true);
        check(alone.Scale == 1 && alone.Game == new Rectangle(0, 44, 1280, 720) && alone.Panel.IsEmpty && alone.Buttons == new Rectangle(1280 - Buttons, 0, Buttons, 44));
        var aloneWide = Compute(new Size(2000, 985), 0, MinPanel, Buttons, true, 1, detached: true);
        check(Math.Abs(aloneWide.Scale - 1.25) < 1e-9 && aloneWide.Game.Left == 200 && aloneWide.Buttons.Right == 1800 && aloneWide.ButtonsScale == 1.25);
        var aloneMin = ClientFor(MinScale, 0);
        var sideDrag = DragSizingDetached(new Rectangle(0, 0, 1600, 788), EdgeRight, aloneMin);
        check(sideDrag.Size == ClientFor(1.25, 0) && sideDrag.Location == Point.Empty);
        var topDrag = DragSizingDetached(Rectangle.FromLTRB(0, -197, 1280, 788), EdgeTop, aloneMin);
        check(topDrag.Size == ClientFor(1.25, 0) && topDrag.Bottom == 788);
        check(DragSizingDetached(new Rectangle(0, 0, 100, 100), EdgeBottomRight, aloneMin).Size == aloneMin);
        // Panel left: the buttons sit over the game header and still scale with it (they must fit its height).
        check(mirroredWide.ButtonsScale == mirroredWide.Scale && mirroredWide.Buttons.Height == mirroredWide.Header.Height
            && wideRight.ButtonsScale == 1 && wideRight.Buttons.Height == 44);
    }
}
