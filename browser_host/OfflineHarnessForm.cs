using System.Drawing;
using System.Drawing.Drawing2D;

namespace MaaShinyColors.BrowserHost;

internal sealed class OfflineHarnessForm : Form
{
    private static readonly Rectangle StateBox = new(340, 269, 600, 96);
    private static readonly Rectangle ClaimButton = new(460, 401, 360, 112);

    private static readonly Color PageColor = Color.FromArgb(15, 23, 42);
    private static readonly Color CardColor = Color.FromArgb(30, 41, 59);
    private static readonly Color BorderColor = Color.FromArgb(71, 85, 105);
    private static readonly Color ReadyStateColor = Color.FromArgb(51, 65, 85);
    private static readonly Color ReadyButtonColor = Color.FromArgb(22, 163, 74);
    private static readonly Color SuccessStateColor = Color.FromArgb(37, 99, 235);

    private bool _completed;

    public OfflineHarnessForm(BrowserHostOptions options)
    {
        Text = options.WindowTitle;
        Name = "MaaShinyColorsOfflineHarness";
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        MinimizeBox = true;
        TopMost = true;
        AutoScaleMode = AutoScaleMode.None;
        ClientSize = new Size(
            BrowserHostOptions.ViewportWidth,
            BrowserHostOptions.ViewportHeight);
        BackColor = PageColor;
        DoubleBuffered = true;
    }

    protected override void OnPaint(PaintEventArgs eventArgs)
    {
        base.OnPaint(eventArgs);
        var graphics = eventArgs.Graphics;
        graphics.SmoothingMode = SmoothingMode.AntiAlias;

        using var titleFont = new Font("Microsoft YaHei UI", 25, FontStyle.Bold);
        using var stateFont = new Font("Microsoft YaHei UI", 21, FontStyle.Bold);
        using var buttonFont = new Font("Microsoft YaHei UI", 25, FontStyle.Bold);
        using var cardBrush = new SolidBrush(CardColor);
        using var borderPen = new Pen(BorderColor, 2);

        var card = new Rectangle(280, 145, 720, 430);
        graphics.FillRectangle(cardBrush, card);
        graphics.DrawRectangle(borderPen, card);

        TextRenderer.DrawText(
            graphics,
            "离线截图点击实验",
            titleFont,
            new Rectangle(340, 197, 600, 48),
            Color.FromArgb(241, 245, 249),
            TextFormatFlags.HorizontalCenter |
            TextFormatFlags.VerticalCenter |
            TextFormatFlags.NoPadding);

        using var stateBrush = new SolidBrush(
            _completed ? SuccessStateColor : ReadyStateColor);
        graphics.FillRectangle(stateBrush, StateBox);
        TextRenderer.DrawText(
            graphics,
            _completed ? "状态：已领取" : "状态：待领取",
            stateFont,
            StateBox,
            Color.White,
            TextFormatFlags.HorizontalCenter |
            TextFormatFlags.VerticalCenter |
            TextFormatFlags.NoPadding);

        using var claimBrush = new SolidBrush(
            _completed ? BorderColor : ReadyButtonColor);
        graphics.FillRectangle(claimBrush, ClaimButton);
        TextRenderer.DrawText(
            graphics,
            _completed ? "已完成" : "领取离线奖励",
            buttonFont,
            ClaimButton,
            Color.White,
            TextFormatFlags.HorizontalCenter |
            TextFormatFlags.VerticalCenter |
            TextFormatFlags.NoPadding);
    }

    protected override void OnMouseUp(MouseEventArgs eventArgs)
    {
        base.OnMouseUp(eventArgs);
        if (
            eventArgs.Button == MouseButtons.Left &&
            !_completed &&
            ClaimButton.Contains(eventArgs.Location)
        )
        {
            _completed = true;
            Invalidate(StateBox);
            Invalidate(ClaimButton);
        }
    }
}
