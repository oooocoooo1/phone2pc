import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:crypto/crypto.dart';

import 'file_transfer_page.dart';

const int protocolVersion = 6;
const int maxClipboardBytes = 256 * 1024;
const int maxHistoryItemBytes = 32 * 1024;
const int maxRemoteInputBytes = 64 * 1024;

// v5.3: 后台通知处理器 (点击复制按钮时复制剪贴板，不打开APP)
@pragma('vm:entry-point')
void notificationTapBackground(NotificationResponse response) {
  // 后台/isolate环境需要初始化bindings才能使用Clipboard
  WidgetsFlutterBinding.ensureInitialized();

  // 从payload获取要复制的内容
  if (response.payload != null && response.payload!.isNotEmpty) {
    Clipboard.setData(ClipboardData(text: response.payload!));
  }
}

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '智连 (v5.7)',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
        useMaterial3: true,
      ),
      home: const MyHomePage(),
    );
  }
}

class MyHomePage extends StatefulWidget {
  const MyHomePage({super.key});

  @override
  State<MyHomePage> createState() => _MyHomePageState();
}

class _MyHomePageState extends State<MyHomePage> with WidgetsBindingObserver {
  // v5.3: Native Method Channel
  static const platform = MethodChannel(
    'io.github.oooocoooo1.phone2pc/channel',
  );
  static const _keepAliveChannel = MethodChannel(
    'io.github.oooocoooo1.phone2pc/keep_alive',
  );
  static const _connectionControlChannel = MethodChannel(
    'io.github.oooocoooo1.phone2pc/connection_control',
  );

  Future<void> _minimizeApp() async {
    try {
      await platform.invokeMethod('minimize');
    } catch (e) {
      debugPrint("Minimize error: $e");
    }
  }

  // WebSocket
  WebSocketChannel? _channel;
  bool _isConnected = false;
  int _connectionGeneration = 0;
  Timer? _handshakeTimer;
  Timer? _reconnectTimer;
  bool _maintainConnection = false;
  bool _isConnecting = false;
  int _reconnectAttempt = 0;
  String? _persistentHost;
  final TextEditingController _ipController = TextEditingController();
  final TextEditingController _pairingCodeController = TextEditingController();
  final TextEditingController _textController = TextEditingController();
  String _deviceId = '';

  // History
  List<String> _ipHistory = [];

  // Clipboard Data
  List<String> _pcHistory = [];
  List<String> _phoneHistory = [];
  String _lastClipboardContent = "";

  Timer? _clipboardTimer;

  // File Transfer State
  final GlobalKey<FileTransferPageState> _fileTransferKey = GlobalKey();
  // UI State
  int _selectedIndex = 0;
  String _statusData = "未连接";
  bool _enterToSend = false; // Default false

  // Notification Plugin (v5.3)
  final FlutterLocalNotificationsPlugin _notificationsPlugin =
      FlutterLocalNotificationsPlugin();
  String? _pendingCopyContent; // 待复制的内容

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _connectionControlChannel.setMethodCallHandler(_handleConnectionControl);
    _loadIpHistory();
    _restorePersistentConnection();
    _initNotifications(); // v5.3
    // 启动剪贴板轮询 (每2秒检查一次)
    _clipboardTimer = Timer.periodic(const Duration(seconds: 2), (timer) {
      if (WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed) {
        _checkClipboard();
      }
    });
  }

  // v5.3: 初始化通知插件
  Future<void> _initNotifications() async {
    try {
      final status = await Permission.notification.request();
      if (status.isDenied) {
        debugPrint('通知权限被拒绝');
      }
      const AndroidInitializationSettings initAndroid =
          AndroidInitializationSettings('@mipmap/ic_launcher');
      const InitializationSettings initSettings = InitializationSettings(
        android: initAndroid,
      );
      await _notificationsPlugin.initialize(
        initSettings,
        onDidReceiveNotificationResponse: _onNotificationTap,
        onDidReceiveBackgroundNotificationResponse: notificationTapBackground,
      );
    } catch (e) {
      debugPrint('Notification initialization error: $e');
    }
  }

  // v5.3: 点击通知复制按钮时复制到剪贴板，然后最小化APP
  Future<void> _onNotificationTap(NotificationResponse response) async {
    final content = response.payload ?? _pendingCopyContent;
    if (content != null && content.isNotEmpty) {
      await Clipboard.setData(ClipboardData(text: content));
      _lastClipboardContent = content;

      // 复制完成后立即最小化到后台
      await _minimizeApp();
    }
  }

  // v5.3: 显示剪贴板同步通知 (带复制按钮，点击走后台处理)
  Future<void> _showClipboardNotification(String content) async {
    // Android notification payloads travel through Binder. Keep large
    // clipboard values out of the payload to avoid transaction failures.
    final canCopyFromNotification =
        utf8.encode(content).length <= maxHistoryItemBytes;
    _pendingCopyContent = canCopyFromNotification ? content : null;
    final displayText = canCopyFromNotification
        ? '收到 ${content.length} 个字符，点击“复制”写入剪贴板'
        : '收到 ${content.length} 个字符，内容较大，请打开应用复制';

    // 使用 BigTextStyleInformation 增加系统展开通知的概率
    final AndroidNotificationDetails androidDetails =
        AndroidNotificationDetails(
          'clipboard_sync',
          '剪贴板同步',
          channelDescription: 'PC剪贴板内容同步通知',
          importance: Importance.max, // 使用 max
          priority: Priority.high,
          ticker: 'PC剪贴板同步',
          autoCancel: true,
          styleInformation: BigTextStyleInformation(displayText), // 展开样式
          actions: canCopyFromNotification
              ? <AndroidNotificationAction>[
                  const AndroidNotificationAction(
                    'copy_action',
                    '复制',
                    icon: DrawableResourceAndroidBitmap('@mipmap/ic_launcher'),
                    showsUserInterface: true,
                    cancelNotification: true,
                  ),
                ]
              : const <AndroidNotificationAction>[],
        );
    final NotificationDetails details = NotificationDetails(
      android: androidDetails,
    );

    await _notificationsPlugin.show(
      1,
      '📋 PC剪贴板 (点击复制按钮)',
      displayText,
      details,
      payload: canCopyFromNotification ? content : null,
    );
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _clipboardTimer?.cancel();
    _handshakeTimer?.cancel();
    _reconnectTimer?.cancel();
    unawaited(_closeChannel(_channel));
    _connectionControlChannel.setMethodCallHandler(null);
    _ipController.dispose();
    _pairingCodeController.dispose();
    _textController.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _checkClipboard();
      if (_maintainConnection && !_isConnected && !_isConnecting) {
        _scheduleReconnect(_persistentHost);
      }
    }
  }

  Future<dynamic> _handleConnectionControl(MethodCall call) async {
    if (call.method == 'disconnectRequested') {
      await _disconnect(status: '已从通知栏主动断开');
    }
    return null;
  }

  Future<void> _restorePersistentConnection() async {
    final prefs = await SharedPreferences.getInstance();
    final shouldMaintain = prefs.getBool('maintain_connection') ?? false;
    final host = prefs.getString('persistent_connection_host')?.trim();
    if (!shouldMaintain || host == null || !_isValidHost(host)) return;

    _maintainConnection = true;
    _persistentHost = host;
    if (_ipController.text.trim().isEmpty) _ipController.text = host;
    if (mounted) setState(() => _statusData = '正在恢复与 $host 的连接...');
    await _startKeepAlive(host);
    await _connect(host, rememberConnection: false);
  }

  Future<void> _rememberPersistentConnection(String host) async {
    _maintainConnection = true;
    _persistentHost = host;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('maintain_connection', true);
    await prefs.setString('persistent_connection_host', host);
    await _startKeepAlive(host);
  }

  Future<void> _disablePersistentConnection() async {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _maintainConnection = false;
    _persistentHost = null;
    _reconnectAttempt = 0;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('maintain_connection', false);
    await prefs.remove('persistent_connection_host');
    try {
      await _keepAliveChannel.invokeMethod('stop');
    } catch (error) {
      debugPrint('Stop keep-alive service error: $error');
    }
  }

  Future<void> _startKeepAlive(String host) async {
    try {
      await _keepAliveChannel.invokeMethod('start', {'host': host});
    } catch (error) {
      debugPrint('Start keep-alive service error: $error');
    }
  }

  void _scheduleReconnect(String? host) {
    if (!_maintainConnection ||
        host == null ||
        !_isValidHost(host) ||
        _isConnected ||
        _isConnecting ||
        _reconnectTimer?.isActive == true) {
      return;
    }
    const delays = [2, 4, 8, 15, 30];
    final delay = delays[_reconnectAttempt.clamp(0, delays.length - 1)];
    _reconnectAttempt++;
    if (mounted) {
      setState(() => _statusData = '连接断开，$delay 秒后自动重连');
    }
    _reconnectTimer = Timer(Duration(seconds: delay), () {
      _reconnectTimer = null;
      if (_maintainConnection && !_isConnected && !_isConnecting) {
        unawaited(_connect(host, rememberConnection: false));
      }
    });
  }

  Future<void> _openAppSettings() async {
    try {
      await platform.invokeMethod('openAppSettings');
    } catch (error) {
      debugPrint('Open app settings error: $error');
    }
  }

  // --- Clipboard Logic ---
  Future<void> _checkClipboard() async {
    ClipboardData? data = await Clipboard.getData(Clipboard.kTextPlain);
    if (!mounted) return;
    if (data != null && data.text != null && data.text!.isNotEmpty) {
      final byteLength = utf8.encode(data.text!).length;
      if (data.text != _lastClipboardContent &&
          byteLength <= maxClipboardBytes) {
        setState(() {
          _lastClipboardContent = data.text!;
          _addToPhoneHistory(_lastClipboardContent);
        });

        // Sync to PC
        if (_isConnected) {
          try {
            await _sendJson({
              "type": "CLIPBOARD_SYNC",
              "source": "PHONE",
              "content": _lastClipboardContent,
            });
          } catch (_) {
            // The connection may close while clipboard access is in progress.
          }
        }
      }
    }
  }

  void _addToPhoneHistory(String text) {
    if (utf8.encode(text).length > maxHistoryItemBytes) return;
    if (_phoneHistory.contains(text)) _phoneHistory.remove(text);
    _phoneHistory.insert(0, text);
    if (_phoneHistory.length > 50) _phoneHistory.removeLast();
    _savePhoneHistory();
  }

  void _addToPcHistory(String text) {
    if (utf8.encode(text).length > maxHistoryItemBytes) return;
    if (_pcHistory.contains(text)) _pcHistory.remove(text);
    _pcHistory.insert(0, text);
    if (_pcHistory.length > 50) _pcHistory.removeLast();
    _savePcHistory();
  }

  // --- WebSocket Logic ---
  Future<void> _connect(
    String ip, {
    bool rememberConnection = true,
  }) async {
    ip = ip.trim();
    if (ip.isEmpty) return;
    if (!_isValidHost(ip)) {
      setState(() => _statusData = "IP 地址或主机名格式无效");
      return;
    }
    if (_isConnecting) return;
    _isConnecting = true;
    if (rememberConnection) {
      await _rememberPersistentConnection(ip);
    }
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    await _ensureDeviceId();
    _saveIpToHistory(ip);

    final int generation = ++_connectionGeneration;
    _handshakeTimer?.cancel();
    await _closeChannel(_channel);

    setState(() {
      _statusData = "正在连接 $ip...";
      _isConnected = false;
    });

    try {
      final host = ip.contains(':') && !ip.startsWith('[') ? '[$ip]' : ip;
      final wsUrl = Uri.parse('ws://$host:8765');
      final socket =
          WebSocket.connect(
                wsUrl.toString(),
                compression: CompressionOptions.compressionOff,
              )
              .timeout(const Duration(seconds: 5))
              .then(
                (webSocket) =>
                    webSocket..pingInterval = const Duration(seconds: 5),
              );
      _channel = IOWebSocketChannel(socket);

      _handshakeTimer = Timer(const Duration(seconds: 8), () {
        if (mounted && generation == _connectionGeneration && !_isConnected) {
          unawaited(_closeChannel(_channel));
          setState(() => _statusData = "连接或鉴权响应超时");
        }
      });
      unawaited(_listenToChannel(_channel!, generation, ip));
    } catch (e) {
      _isConnecting = false;
      if (mounted && generation == _connectionGeneration) {
        setState(() => _statusData = "连接异常: $e");
      }
      if (generation == _connectionGeneration) _scheduleReconnect(ip);
    }
  }

  Future<void> _listenToChannel(
    WebSocketChannel channel,
    int generation,
    String ip,
  ) async {
    try {
      await for (final message in channel.stream) {
        if (!mounted || generation != _connectionGeneration) return;
        if (message is String) {
          await _handleStringMessage(message, generation, ip);
        } else if (message is List<int>) {
          await _fileTransferKey.currentState?.handleBinaryMessage(message);
        }
      }
    } catch (e) {
      if (mounted && generation == _connectionGeneration) {
        setState(() {
          _isConnected = false;
          _statusData = "连接错误: $e";
        });
      }
    } finally {
      if (mounted && generation == _connectionGeneration) {
        _isConnecting = false;
        _handshakeTimer?.cancel();
        _fileTransferKey.currentState?.handleDisconnect();
        setState(() {
          _isConnected = false;
          if (!_statusData.startsWith('连接错误') &&
              !_statusData.startsWith('鉴权失败') &&
              !_statusData.startsWith('请输入') &&
              !_statusData.startsWith('协议版本')) {
            _statusData = "连接断开";
          }
        });
        _scheduleReconnect(ip);
      }
    }
  }

  Future<void> _handleStringMessage(
    String message,
    int generation,
    String ip,
  ) async {
    Map<String, dynamic> data;
    try {
      final decoded = jsonDecode(message);
      if (decoded is! Map<String, dynamic>) return;
      data = decoded;
    } catch (_) {
      return;
    }

    final type = data['type'];
    if (type == 'AUTH_REQUIRED') {
      if (data['protocol'] != protocolVersion) {
        setState(() => _statusData = "协议版本不兼容，请更新两端应用");
        await _disablePersistentConnection();
        await _channel?.sink.close();
        return;
      }
      final prefs = await SharedPreferences.getInstance();
      final token = prefs.getString('auth_token_$ip');
      if (!mounted || generation != _connectionGeneration) return;
      if (token != null && token.isNotEmpty) {
        final challenge = data['challenge'];
        if (challenge is! String || challenge.isEmpty) {
          setState(() => _statusData = "服务端鉴权挑战无效");
          await _channel?.sink.close();
          return;
        }
        final response = Hmac(
          sha256,
          utf8.encode(token),
        ).convert(utf8.encode(challenge)).toString();
        _channel?.sink.add(
          jsonEncode({
            "type": "AUTH",
            "device_id": _deviceId,
            "response": response,
          }),
        );
      } else {
        final code = _pairingCodeController.text.trim();
        if (!RegExp(r'^\d{6}$').hasMatch(code)) {
          setState(() => _statusData = "请输入 PC 显示的 6 位配对码后重新连接");
          await _disablePersistentConnection();
          await _channel?.sink.close();
          return;
        }
        _channel?.sink.add(
          jsonEncode({"type": "PAIR", "device_id": _deviceId, "code": code}),
        );
      }
      return;
    }

    if (type == 'PAIR_SUCCESS' || type == 'AUTH_OK') {
      if (data['protocol'] != protocolVersion) {
        setState(() => _statusData = "协议版本不兼容，请更新两端应用");
        await _disablePersistentConnection();
        await _channel?.sink.close();
        return;
      }
      if (type == 'PAIR_SUCCESS' && data['token'] is String) {
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString('auth_token_$ip', data['token']);
      }
      if (!mounted || generation != _connectionGeneration) return;
      _handshakeTimer?.cancel();
      _isConnecting = false;
      _reconnectAttempt = 0;
      setState(() {
        _isConnected = true;
        _statusData = "已安全连接到 $ip";
        _lastClipboardContent = "";
        _pairingCodeController.clear();
      });
      await _checkClipboard();
      return;
    }

    if (type == 'AUTH_ERROR') {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove('auth_token_$ip');
      if (mounted && generation == _connectionGeneration) {
        setState(() => _statusData = "鉴权失败，请输入新的配对码");
      }
      await _disablePersistentConnection();
      await _channel?.sink.close();
      return;
    }

    if (!_isConnected) return;
    if (type == 'ERROR') {
      final message = data['message']?.toString() ?? '服务端拒绝了请求';
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(message)));
      }
      return;
    }
    if (type == 'CLIPBOARD_SYNC' &&
        data['source'] == 'PC' &&
        data['content'] is String) {
      final content = data['content'] as String;
      if (utf8.encode(content).length <= maxClipboardBytes) {
        _lastClipboardContent = content;
        _addToPcHistory(content);
        if (mounted) setState(() {});
        unawaited(_showClipboardNotification(content));
      }
    } else if (type is String && type.startsWith('FILE_')) {
      await _fileTransferKey.currentState?.handleFileMessage(data);
    }
  }

  void _sendMessage() {
    if (!_isConnected) {
      _tryAutoConnect();
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text("正在连接，请连接成功后再次发送")));
      return;
    }
    if (_textController.text.isNotEmpty) {
      if (utf8.encode(_textController.text).length > maxRemoteInputBytes) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text("输入内容不能超过 64 KiB")));
        return;
      }
      _channel?.sink.add(_textController.text);
      _textController.clear();
    }
  }

  void _tryAutoConnect() {
    if (_ipController.text.isNotEmpty) {
      _connect(_ipController.text);
    } else if (_ipHistory.isNotEmpty) {
      _connect(_ipHistory.first);
    }
  }

  Future<void> _sendJson(Map<String, dynamic> data) async {
    if (!_isConnected || _channel == null) {
      throw StateError('未连接');
    }
    _channel!.sink.add(jsonEncode(data));
  }

  Future<void> _sendBinary(Uint8List data) async {
    if (!_isConnected || _channel == null) {
      throw StateError('未连接');
    }
    _channel!.sink.add(data);
    // Yield so socket events and ACKs can be processed during large transfers.
    await Future<void>.delayed(Duration.zero);
  }

  Future<void> _disconnect({String status = "已手动断开"}) async {
    ++_connectionGeneration;
    _handshakeTimer?.cancel();
    _isConnecting = false;
    await _disablePersistentConnection();
    final channel = _channel;
    _channel = null;
    _fileTransferKey.currentState?.handleDisconnect();
    if (mounted) {
      setState(() {
        _isConnected = false;
        _statusData = status;
      });
    }
    await _closeChannel(channel);
  }

  Future<void> _closeChannel(WebSocketChannel? channel) async {
    if (channel == null) return;
    try {
      await channel.sink.close();
    } catch (error) {
      debugPrint('Close WebSocket error: $error');
    }
  }

  bool _isValidHost(String value) {
    if (value.length > 253 || value.contains(RegExp(r'[\s/\\]'))) return false;
    return RegExp(r'^[A-Za-z0-9.:\-\[\]]+$').hasMatch(value);
  }

  Future<void> _ensureDeviceId() async {
    if (_deviceId.isNotEmpty) return;
    final prefs = await SharedPreferences.getInstance();
    _deviceId = prefs.getString('device_id') ?? '';
    if (_deviceId.isEmpty) {
      final random = Random.secure();
      final bytes = List<int>.generate(24, (_) => random.nextInt(256));
      _deviceId = base64UrlEncode(bytes);
      await prefs.setString('device_id', _deviceId);
    }
  }

  // --- History Logic ---
  Future<void> _loadIpHistory() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _ipHistory = (prefs.getStringList('ip_history') ?? [])
          .where(_isValidHost)
          .take(5)
          .toList();
      if (_ipHistory.isNotEmpty && _ipController.text.isEmpty) {
        _ipController.text = _ipHistory.first;
      }
      // 加载剪贴板历史
      _pcHistory = (prefs.getStringList('pc_history') ?? [])
          .where((item) => utf8.encode(item).length <= maxHistoryItemBytes)
          .take(50)
          .toList();
      _phoneHistory = (prefs.getStringList('phone_history') ?? [])
          .where((item) => utf8.encode(item).length <= maxHistoryItemBytes)
          .take(50)
          .toList();
      _enterToSend = prefs.getBool('enter_to_send') ?? false;
    });
  }

  Future<void> _saveEnterToSend() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('enter_to_send', _enterToSend);
  }

  Future<void> _savePcHistory() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList('pc_history', _pcHistory);
  }

  Future<void> _savePhoneHistory() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList('phone_history', _phoneHistory);
  }

  Future<void> _saveIpToHistory(String ip) async {
    if (_ipHistory.contains(ip)) {
      _ipHistory.remove(ip);
    }
    _ipHistory.insert(0, ip);
    if (_ipHistory.length > 5) _ipHistory.removeLast();

    if (mounted) setState(() {});
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList('ip_history', _ipHistory);
  }

  Future<void> _deleteIp(String ip) async {
    setState(() {
      _ipHistory.remove(ip);
    });
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList('ip_history', _ipHistory);
  }

  // --- UI Construction ---
  @override
  Widget build(BuildContext context) {
    // 按返回键时最小化到后台，不断开连接
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, result) {
        if (!didPop) _minimizeApp();
      },
      child: Scaffold(
        appBar: AppBar(
          title: const Text('智连 Phone2PC'),
          backgroundColor: Theme.of(context).colorScheme.inversePrimary,
          actions: [
            Padding(
              padding: const EdgeInsets.only(right: 16),
              child: GestureDetector(
                onTap: () {
                  if (_isConnected || _maintainConnection) {
                    // Manual Disconnect
                    _disconnect();
                  } else {
                    // Manual Connect (Retry last IP)
                    if (_ipController.text.isNotEmpty) {
                      _connect(_ipController.text);
                    } else if (_ipHistory.isNotEmpty) {
                      _ipController.text = _ipHistory.first;
                      _connect(_ipHistory.first);
                    } else {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text("请输入 IP 地址")),
                      );
                    }
                  }
                },
                child: Icon(
                  _isConnected
                      ? Icons.link
                      : _maintainConnection
                      ? Icons.sync
                      : Icons.link_off,
                  color: _isConnected
                      ? Colors.green
                      : _maintainConnection
                      ? Colors.orange
                      : Colors.grey,
                ),
              ),
            ),
          ],
        ),
        body: IndexedStack(
          // Use IndexedStack to keep state alive
          index: _selectedIndex,
          children: [
            _buildInputPage(),
            _buildClipboardPage(),
            FileTransferPage(
              // [NEW]
              key: _fileTransferKey,
              onSendJson: _sendJson,
              onSendBinary: _sendBinary,
              peerHost: _ipController.text.trim().replaceAll(
                RegExp(r'^\[|\]$'),
                '',
              ),
            ),
          ],
        ),
        bottomNavigationBar: BottomNavigationBar(
          items: const [
            BottomNavigationBarItem(icon: Icon(Icons.keyboard), label: '输入'),
            BottomNavigationBarItem(icon: Icon(Icons.copy), label: '云剪贴板'),
            BottomNavigationBarItem(icon: Icon(Icons.folder), label: '文件'),
          ],
          currentIndex: _selectedIndex,
          onTap: (index) => setState(() => _selectedIndex = index),
        ),
      ),
    );
  }

  Widget _buildInputPage() {
    return Column(
      children: [
        // Status
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(8),
          color: _isConnected
              ? Colors.green.shade100
              : _maintainConnection
              ? Colors.orange.shade100
              : Colors.red.shade100,
          child: Text(_statusData, textAlign: TextAlign.center),
        ),

        if (_maintainConnection)
          Container(
            width: double.infinity,
            padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
            color: Colors.blueGrey.shade50,
            child: Row(
              children: [
                const Icon(Icons.shield_outlined, size: 20),
                const SizedBox(width: 8),
                const Expanded(
                  child: Text(
                    '后台常驻已开启。小米/HyperOS 建议允许自启动，并把电量策略设为“无限制”。',
                    style: TextStyle(fontSize: 12),
                  ),
                ),
                TextButton(
                  onPressed: _openAppSettings,
                  child: const Text('系统设置'),
                ),
              ],
            ),
          ),

        // Connection Area
        if (!_isConnected) ...[
          Padding(
            padding: const EdgeInsets.all(16.0),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _ipController,
                    decoration: const InputDecoration(
                      labelText: '输入 PC IP 地址',
                      border: OutlineInputBorder(),
                      hintText: '192.168.x.x',
                    ),
                    keyboardType: TextInputType.url,
                  ),
                ),
                const SizedBox(width: 10),
                ElevatedButton(
                  onPressed: _maintainConnection
                      ? () => _disconnect(status: '已停止常驻连接')
                      : () => _connect(_ipController.text),
                  child: Text(_maintainConnection ? '停止' : '连接'),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: TextField(
              controller: _pairingCodeController,
              decoration: const InputDecoration(
                labelText: '首次连接配对码',
                hintText: '输入 PC 窗口显示的 6 位数字',
                border: OutlineInputBorder(),
                prefixIcon: Icon(Icons.security),
              ),
              keyboardType: TextInputType.number,
              maxLength: 6,
              obscureText: true,
            ),
          ),
          // History List
          Expanded(
            child: ListView.builder(
              itemCount: _ipHistory.length,
              itemBuilder: (ctx, i) {
                final ip = _ipHistory[i];
                return ListTile(
                  leading: const Icon(Icons.history),
                  title: Text(ip),
                  onTap: () {
                    _ipController.text = ip;
                    _connect(ip);
                  },
                  trailing: IconButton(
                    icon: const Icon(Icons.delete_outline),
                    onPressed: () => _deleteIp(ip),
                  ),
                );
              },
            ),
          ),
        ] else ...[
          // Input Area
          Expanded(
            child: SingleChildScrollView(
              child: Padding(
                padding: const EdgeInsets.all(20.0),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.start, // Top aligned
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const SizedBox(height: 10), // Reduced spacer
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: Colors.blue.shade50,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: const Row(
                        children: [
                          Icon(Icons.link, size: 20, color: Colors.blue),
                          SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              "已连接，在此输入文字发送给 PC",
                              style: TextStyle(color: Colors.blue),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 20),

                    // Input Field
                    TextField(
                      controller: _textController,
                      autofocus: true,
                      decoration: const InputDecoration(
                        border: OutlineInputBorder(),
                        labelText: '发送内容',
                        alignLabelWithHint: true,
                      ),
                      minLines: 3,
                      maxLines: 8, // Increased height
                      maxLength: 65536,
                      onChanged: (val) {
                        if (_enterToSend && val.endsWith("\n")) {
                          // Remove the newline
                          _textController.text = val.substring(
                            0,
                            val.length - 1,
                          );
                          _textController
                              .selection = TextSelection.fromPosition(
                            TextPosition(offset: _textController.text.length),
                          );
                          _sendMessage();
                        }
                      },
                    ),
                    const SizedBox(height: 10),

                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        // Switch
                        Row(
                          children: [
                            Switch(
                              value: _enterToSend,
                              onChanged: (val) {
                                setState(() => _enterToSend = val);
                                _saveEnterToSend();
                              },
                            ),
                            const Text("即输即发(回车发送)"),
                          ],
                        ),

                        ElevatedButton.icon(
                          onPressed: _sendMessage,
                          icon: const Icon(Icons.send),
                          label: const Text("发送"),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ],
    );
  }

  Widget _buildClipboardPage() {
    return DefaultTabController(
      length: 2,
      child: Column(
        children: [
          const TabBar(
            tabs: [
              Tab(text: "PC 剪贴板"),
              Tab(text: "本机历史"),
            ],
          ),
          Expanded(
            child: TabBarView(
              children: [
                _buildHistoryList(_pcHistory, true),
                _buildHistoryList(_phoneHistory, false),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHistoryList(List<String> list, bool isPcSource) {
    if (list.isEmpty) return const Center(child: Text("暂无记录"));
    return ListView.separated(
      itemCount: list.length,
      separatorBuilder: (_, __) => const Divider(height: 1),
      itemBuilder: (ctx, i) {
        final text = list[i];
        return ListTile(
          title: Text(text, maxLines: 2, overflow: TextOverflow.ellipsis),
          subtitle: Text(isPcSource ? "来自 PC" : "来自 本机"),
          onTap: () {
            Clipboard.setData(ClipboardData(text: text));
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(
                content: Text("已复制到剪贴板"),
                duration: Duration(milliseconds: 500),
              ),
            );
          },
          trailing: IconButton(
            icon: const Icon(
              Icons.delete_outline,
              size: 20,
              color: Colors.grey,
            ),
            onPressed: () {
              setState(() {
                list.removeAt(i);
                if (isPcSource) {
                  _savePcHistory();
                } else {
                  _savePhoneHistory();
                }
              });
            },
          ),
        );
      },
    );
  }
}
