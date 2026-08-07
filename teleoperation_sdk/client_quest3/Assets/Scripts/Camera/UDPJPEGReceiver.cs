using System;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// Receives JPEG frames over UDP and displays them on a RawImage.
/// Pure C# software decode via Texture2D.LoadImage — no MediaCodec,
/// no native plugin, no OOM risk.
///
/// Protocol (each UDP datagram):
///   [4B LE frame_id][4B LE chunk_idx][4B LE total_chunks][4B LE jpeg_size][payload]
///
/// Fixed chunk payload size = 60000 bytes.  Offset = chunk_idx * 60000.
/// Most frames fit in a single datagram (total_chunks=1).
///
/// Usage: attach to the same GameObject as RemoteCameraWindow, assign
/// targetImage to the RawImage.  Starts listening in OnEnable.
/// </summary>
public class UDPJPEGReceiver : MonoBehaviour
{
    [SerializeField] private RawImage targetImage;
    [SerializeField] private int listenPort = 10004;

    private const int MAX_CHUNK = 1400;
    private const int HEADER_SIZE = 16;
    private const int MAX_CHUNKS = 256;

    private UdpClient _udpClient;
    private Thread _recvThread;
    private volatile bool _running;

    // Reassembly buffers (accessed only from recv thread, except _ready* under lock)
    private byte[] _jpegBuffer;
    private int _jpegSize;
    private int _currentFrameId = -1;
    private int _totalChunks;
    private int _chunksReceived;
    private readonly bool[] _chunkMask = new bool[MAX_CHUNKS];

    // Handoff to main thread
    private byte[] _readyFrame;
    private int _readyFrameSize;
    private int _readyFrameId = -1;
    private readonly object _readyLock = new object();

    private Texture2D _texture;

    // ── Unity lifecycle ──────────────────────────────────────────────────

    private void Awake()
    {
        // Self-configure: find RawImage in children if not assigned in Inspector
        if (targetImage == null)
            targetImage = GetComponentInChildren<RawImage>(true);
        Debug.Log($"[UDPJPEG] Awake — targetImage={targetImage?.name ?? "NULL"} port={listenPort}");
    }

    private void OnEnable()
    {
        if (targetImage == null)
            targetImage = GetComponentInChildren<RawImage>(true);

        _running = true;
        try
        {
            _recvThread = new Thread(ReceiveLoop)
            {
                Name = "UDPJPEGReceiver",
                IsBackground = true,
            };
            _recvThread.Start();
            Debug.Log($"[UDPJPEG] listening on port {listenPort}");
        }
        catch (System.Exception e)
        {
            Debug.LogError($"[UDPJPEG] failed to start thread: {e.Message}");
        }
    }

    private void OnDisable()
    {
        _running = false;
        try { _udpClient?.Close(); } catch { }
        _recvThread?.Join(2000);
        _udpClient?.Dispose();
        _udpClient = null;
        Debug.Log("[UDPJPEG] stopped");
    }

    private void Update()
    {
        // Non-blocking handoff from recv thread to main thread
        byte[] frame;
        int frameSize;
        int frameId;
        lock (_readyLock)
        {
            if (_readyFrame == null || _readyFrameId == _lastDisplayedFrameId)
                return;
            frame = _readyFrame;
            frameSize = _readyFrameSize;
            frameId = _readyFrameId;
            _lastDisplayedFrameId = frameId;
        }

        if (targetImage == null) return;

        if (_texture == null)
            _texture = new Texture2D(2, 2);

        if (_texture.LoadImage(frame))
        {
            targetImage.texture = _texture;
            if (_displayedCount == 0)
                Debug.Log($"[UDPJPEG] first frame displayed — {frameSize} bytes, texture={_texture.width}x{_texture.height}");
            _displayedCount++;
        }
        else
        {
            Debug.LogWarning("[UDPJPEG] LoadImage failed — corrupt JPEG?");
        }
    }
    private int _lastDisplayedFrameId = -1;
    private int _displayedCount;
    private int _receivedDatagrams;
    private int _completedFrames;

    // ── UDP receive thread ───────────────────────────────────────────────

    private void ReceiveLoop()
    {
        try
        {
            _udpClient = new UdpClient(listenPort);
            _udpClient.Client.ReceiveTimeout = 1000;
        }
        catch (Exception e)
        {
            Debug.LogError($"[UDPJPEG] failed to bind UDP port {listenPort}: {e.Message}");
            return;
        }

        var remote = new IPEndPoint(IPAddress.Any, 0);
        var headerBuf = new byte[HEADER_SIZE];

        while (_running)
        {
            byte[] datagram;
            try
            {
                datagram = _udpClient.Receive(ref remote);
            }
            catch (SocketException)
            {
                continue; // timeout or close — loop checks _running
            }
            catch (ObjectDisposedException)
            {
                break;
            }

            if (datagram == null || datagram.Length < HEADER_SIZE)
                continue;

            _receivedDatagrams++;
            if (_receivedDatagrams == 1)
                Debug.Log($"[UDPJPEG] first datagram received — {datagram.Length} bytes");

            int frameId  = BitConverter.ToInt32(datagram, 0);
            int chunkIdx = BitConverter.ToInt32(datagram, 4);
            int totalChk = BitConverter.ToInt32(datagram, 8);
            int jpegSize = BitConverter.ToInt32(datagram, 12);

            if (totalChk <= 0 || totalChk > MAX_CHUNKS || jpegSize <= 0)
                continue;

            int payloadLen = datagram.Length - HEADER_SIZE;
            if (payloadLen <= 0)
                continue;

            // New frame starts
            if (frameId != _currentFrameId)
            {
                _currentFrameId = frameId;
                _jpegSize = jpegSize;
                _totalChunks = totalChk;
                _chunksReceived = 0;
                Array.Clear(_chunkMask, 0, totalChk);
                if (_jpegBuffer == null || _jpegBuffer.Length < jpegSize)
                    _jpegBuffer = new byte[jpegSize];
            }

            if (jpegSize != _jpegSize || totalChk != _totalChunks)
                continue; // stale datagram from different frame — drop

            if (chunkIdx >= totalChk || _chunkMask[chunkIdx])
                continue; // duplicate — drop

            int offset = chunkIdx * MAX_CHUNK;
            int copyLen = Math.Min(payloadLen, jpegSize - offset);
            if (copyLen <= 0) continue;

            Buffer.BlockCopy(datagram, HEADER_SIZE, _jpegBuffer, offset, copyLen);
            _chunkMask[chunkIdx] = true;
            _chunksReceived++;

            // Frame complete?
            if (_chunksReceived >= totalChk)
            {
                _completedFrames++;
                if (_completedFrames == 1 || _completedFrames % 150 == 0)
                    Debug.Log($"[UDPJPEG] frame #{_completedFrames} complete — {_jpegSize} bytes, {_totalChunks} chunk(s)");
                lock (_readyLock)
                {
                    // Swap buffer — avoid allocating every frame
                    _readyFrame = _jpegBuffer;
                    _readyFrameSize = _jpegSize;
                    _readyFrameId = frameId;
                    // Allocate fresh buffer for next frame
                    _jpegBuffer = new byte[Math.Max(_jpegSize, 65536)];
                }
                _currentFrameId = -1; // force new-frame detection
            }
        }
    }
}
