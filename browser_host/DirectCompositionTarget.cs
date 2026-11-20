using System.Runtime.InteropServices;

namespace MaaShinyColors.BrowserHost;

/// <summary>Minimal official DirectComposition visual tree for native WebView2 hosting.</summary>
internal sealed class DirectCompositionTarget : IDisposable
{
    // Interface order and identifiers follow Windows SDK dcomp.h.
    [ComImport, Guid("C37EA93A-E7AA-450D-B16F-9746CB0407F3"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IDevice
    {
        void Commit();
        void WaitForCommitCompletion();
        void GetFrameStatistics(IntPtr statistics);
        void CreateTargetForHwnd(IntPtr window, [MarshalAs(UnmanagedType.Bool)] bool topmost, out ITarget target);
        void CreateVisual([MarshalAs(UnmanagedType.IUnknown)] out object visual);
    }
    [ComImport, Guid("EACDD04C-117E-4E17-88F4-D1B12B0E3D89"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface ITarget
    {
        void SetRoot([MarshalAs(UnmanagedType.IUnknown)] object? visual);
    }
    /// <summary>
    /// IDCompositionVisual (dcomp.h). MSVC lays out each overloaded pair in reverse declaration order, so the vtable has
    /// the object/animation overload before the value overload (SetOffsetX(animation), SetOffsetX(float), ...,
    /// SetTransform(IDCompositionTransform*), SetTransform(const D2D_MATRIX_3X2_F&)). Only SetTransform(matrix) and
    /// SetBitmapInterpolationMode are called; the other slots are placeholders that keep the order and are never used.
    /// </summary>
    [ComImport, Guid("4D93059D-097B-4651-9A60-F0F25116E2F3"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IVisual
    {
        void SetOffsetXAnimation(IntPtr animation);
        void SetOffsetX(float offsetX);
        void SetOffsetYAnimation(IntPtr animation);
        void SetOffsetY(float offsetY);
        void SetTransformObject(IntPtr transform);
        void SetTransform(ref System.Numerics.Matrix3x2 matrix);
        void SetTransformParent(IntPtr visual);
        void SetEffect(IntPtr effect);
        void SetBitmapInterpolationMode(int mode);
    }
    private const int InterpolationLinear = 1;   // DCOMPOSITION_BITMAP_INTERPOLATION_MODE_LINEAR
    [DllImport("dcomp.dll", PreserveSig = false)]
    private static extern void DCompositionCreateDevice(IntPtr dxgiDevice, ref Guid iid, out IDevice device);
    private readonly IDevice _device;
    private readonly ITarget _target;
    public object Visual { get; }

    public DirectCompositionTarget(IntPtr window)
    {
        var iid = typeof(IDevice).GUID;
        DCompositionCreateDevice(IntPtr.Zero, ref iid, out _device);
        _device.CreateTargetForHwnd(window, true, out _target);
        _device.CreateVisual(out var visual);
        Visual = visual;
        _target.SetRoot(Visual);
    }
    public void Commit() => _device.Commit();

    /// <summary>
    /// Display-only scale of the root visual (the WebView content under it keeps its own 1280x720 size; captures and
    /// SendMouseInput use that content, never this transform). Throws a COMException on failure; the caller then keeps
    /// the game at 1:1. Linear interpolation keeps the stretched picture smooth.
    /// </summary>
    public void SetScale(float scaleX, float scaleY)
    {
        var visual = (IVisual)Visual;
        var matrix = System.Numerics.Matrix3x2.CreateScale(scaleX, scaleY);
        visual.SetBitmapInterpolationMode(InterpolationLinear);
        visual.SetTransform(ref matrix);
        _device.Commit();
    }
    public void Dispose()
    {
        _target.SetRoot(null);
        _device.Commit();
        Marshal.ReleaseComObject(Visual);
        Marshal.ReleaseComObject(_target);
        Marshal.ReleaseComObject(_device);
    }
}
