import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'file_transfer_page.dart';

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '智连 (v5.2)',
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
  // WebSocket
  WebSocketChannel? _channel;
  bool _isConnected = false;
  final TextEditingController _ipController = TextEditingController();
  final TextEditingController _textController = TextEditingController();
  
  // History
  List<String> _ipHistory = [];
  
  // Clipboard Data
  List<String> _pcHistory = [];
  List<String> _phoneHistory = [];
  String _lastClipboardContent = "";

  Timer? _clipboardTimer;
  
  // File Transfer State
  final GlobalKey<FileTransferPageState> _fileTransferKey = GlobalKey();
  final List<String> _transferLogs = [];

  // UI State
  int _selectedIndex = 0;
  String _statusData = "未连接";
  bool _enterToSend = false; // Default false

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadIpHistory();
    // 启动剪贴板轮询 (每2秒检查一次)
    _clipboardTimer = Timer.periodic(const Duration(seconds: 2), (timer) {
      if (WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed) {
        _checkClipboard();
      }
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _clipboardTimer?.cancel();
    _channel?.sink.close();
    _ipController.dispose();
    _textController.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _checkClipboard();
    }
  }

  // --- Clipboard Logic ---
  Future<void> _checkClipboard() async {
    ClipboardData? data = await Clipboard.getData(Clipboard.kTextPlain);
    if (data != null && data.text != null && data.text!.isNotEmpty) {
      if (data.text != _lastClipboardContent) {
        setState(() {
          _lastClipboardContent = data.text!;
          _addToPhoneHistory(_lastClipboardContent);
        });
        
        // Sync to PC
        if (_isConnected) {
          _sendJson({"type": "CLIPBOARD_SYNC", "source": "PHONE", "content": _lastClipboardContent});
        }
      }
    }
  }

  void _addToPhoneHistory(String text) {
    if (_phoneHistory.contains(text)) _phoneHistory.remove(text);
    _phoneHistory.insert(0, text);
    if (_phoneHistory.length > 200) _phoneHistory.removeLast();
    _savePhoneHistory();
  }

  void _addToPcHistory(String text) {
    if (_pcHistory.contains(text)) _pcHistory.remove(text);
    _pcHistory.insert(0, text);
    if (_pcHistory.length > 200) _pcHistory.removeLast();
    _savePcHistory();
  }

  // --- WebSocket Logic ---
  Future<void> _connect(String ip) async {
    if (ip.isEmpty) return;
    _saveIpToHistory(ip);
    
    setState(() {
      _statusData = "正在连接 $ip...";
      _isConnected = false;
    });

    try {
      final wsUrl = Uri.parse('ws://$ip:8765');
      // Create channel with pingInterval for keepalive (Background Stability)
      _channel = IOWebSocketChannel.connect(wsUrl, pingInterval: const Duration(seconds: 5));
      
      // Handshake Timeout Timer
      Timer(const Duration(seconds: 3), () {
          if (mounted && !_isConnected) {
              _channel?.sink.close();
              setState(() => _statusData = "连接响应超时");
          }
      });

      // Listen to stream
      _channel!.stream.listen((message) {
          // [Handshake Success] Check on first message
          if (!_isConnected && mounted) {
               setState(() {
                   _isConnected = true;
                   _statusData = "已连接到 $ip";
                   _lastClipboardContent = ""; // Reset for sync
               });
               _checkClipboard(); // Initial sync
          }

          if (message is String) {
              try {
                 if (message.startsWith('{') && message.contains('"type":')) {
                     final data = jsonDecode(message);
                     
                     if (data['type'] == 'WELCOME') {
                         return; // Handshake ack
                     }
                     
                     if (data['type'] == 'FILE_OFFER' || data['type'] == 'ACK') {
                        _fileTransferKey.currentState?.handleFileMessage(data);
                        return;
                     }
                 }
              } catch(e) {}
              
              if (mounted) {
                  setState(() {
                      _lastClipboardContent = message;
                      _handleMessage(message); 
                  });
              }
          } else if (message is List<int>) {
               _fileTransferKey.currentState?.handleBinaryMessage(message);
          }
      }, onDone: () {
        if (mounted) {
            setState(() {
            _isConnected = false;
            _statusData = "连接断开";
            });
        }
      }, onError: (error) {
        if (mounted) {
            setState(() {
            _isConnected = false;
            _statusData = "连接错误: $error";
            });
        }
      });
      
      // Note: We DO NOT set _isConnected = true here anymore.
      // It happens inside listen() when the WELCOME message (or any message) arrives.

    } catch (e) {
      if (mounted) {
          setState(() => _statusData = "连接异常: $e");
      }
    }
  }

  void _handleMessage(dynamic message) {
    try {
      // Try verify JSON
      final data = jsonDecode(message);
      final type = data['type'];
      
      if (type == 'CLIPBOARD_SYNC' && data['source'] == 'PC') {
        final content = data['content'];
        setState(() {
          _addToPcHistory(content);
        });
      } else if (type != null && type.toString().startsWith('FILE_')) {
         // Route to FileTransferPage [NEW]
         _fileTransferKey.currentState?.handleFileMessage(data);
      }
    } catch (e) {
      // Not JSON or other message
    }
  }

  void _sendMessage() {
    if (!_isConnected) {
       _tryAutoConnect();
    }
    
    // Slight delay to allow connection
    Future.delayed(const Duration(milliseconds: 200), () {
        if (_isConnected && _textController.text.isNotEmpty) {
          _channel?.sink.add(_textController.text);
          _textController.clear();
        } else if (!_isConnected) {
            ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("无法连接到 PC，请检查网络或 IP")));
        }
    });
  }

  void _tryAutoConnect() {
      if (_ipController.text.isNotEmpty) {
          _connect(_ipController.text);
      } else if (_ipHistory.isNotEmpty) {
          _connect(_ipHistory.first);
      }
  }
  
  void _sendJson(Map<String, dynamic> data) {
    if (!_isConnected) {
        _tryAutoConnect();
    }
    Future.delayed(const Duration(milliseconds: 200), () {
        if (_isConnected) {
          _channel?.sink.add(jsonEncode(data));
        } else {
             // For clipboard sync, we might suppress error or show subtle toast
             // For file transfer, it will be handled by UI
             if (data['type'] != 'CLIPBOARD_SYNC') {
                 ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("发送失败：未连接")));
             }
        }
    });
  }

  void _sendBinary(List<int> data) {
      if (_isConnected && _channel != null) {
          _channel!.sink.add(data);
      }
  }

  // --- History Logic ---
  Future<void> _loadIpHistory() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _ipHistory = prefs.getStringList('ip_history') ?? [];
      if (_ipHistory.isNotEmpty && _ipController.text.isEmpty) {
        _ipController.text = _ipHistory.first;
      }
      // 加载剪贴板历史
      _pcHistory = prefs.getStringList('pc_history') ?? [];
      _phoneHistory = prefs.getStringList('phone_history') ?? [];
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
    return Scaffold(
      appBar: AppBar(
        title: const Text('智连 Phone2PC'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
        actions: [
            Padding(
              padding: const EdgeInsets.only(right: 16),
              child: GestureDetector(
                onTap: () {
                  if (_isConnected) {
                    // Manual Disconnect
                    _channel?.sink.close();
                    setState(() {
                      _isConnected = false;
                      _statusData = "已手动断开";
                    });
                  } else {
                    // Manual Connect (Retry last IP)
                    if (_ipController.text.isNotEmpty) {
                      _connect(_ipController.text);
                    } else if (_ipHistory.isNotEmpty) {
                      _ipController.text = _ipHistory.first;
                      _connect(_ipHistory.first);
                    } else {
                        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("请输入 IP 地址")));
                    }
                  }
                },
                child: Icon(
                  _isConnected ? Icons.link : Icons.link_off, 
                  color: _isConnected ? Colors.green : Colors.grey
                )
              ),
            )
        ],
      ),
      body: IndexedStack( // Use IndexedStack to keep state alive
        index: _selectedIndex,
        children: [
            _buildInputPage(),
            _buildClipboardPage(),
            FileTransferPage( // [NEW]
                key: _fileTransferKey,
                onSendJson: _sendJson,
                onSendBinary: _sendBinary,
            )
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
    );
  }

  Widget _buildInputPage() {
    return Column(
      children: [
        // Status
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(8),
          color: _isConnected ? Colors.green.shade100 : Colors.red.shade100,
          child: Text(_statusData, textAlign: TextAlign.center),
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
                      hintText: '192.168.x.x'
                    ),
                    keyboardType: TextInputType.number,
                  ),
                ),
                const SizedBox(width: 10),
                ElevatedButton(
                  onPressed: () => _connect(_ipController.text),
                  child: const Text('连接'),
                ),
              ],
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
          )
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
                              borderRadius: BorderRadius.circular(8)
                          ),
                          child: Row(
                            children: [
                                const Icon(Icons.link, size: 20, color: Colors.blue),
                                const SizedBox(width: 8),
                                const Expanded(child: Text("已连接，在此输入文字发送给 PC", style: TextStyle(color: Colors.blue))),
                            ],
                          )
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
                            onChanged: (val) {
                                if (_enterToSend && val.endsWith("\n")) {
                                    // Remove the newline
                                    _textController.text = val.substring(0, val.length - 1);
                                    _textController.selection = TextSelection.fromPosition(TextPosition(offset: _textController.text.length));
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
                                            }
                                        ),
                                        const Text("即输即发(回车发送)"),
                                    ],
                                ),
                                
                                ElevatedButton.icon(
                                    onPressed: _sendMessage, 
                                    icon: const Icon(Icons.send), 
                                    label: const Text("发送")
                                )
                            ],
                        )
                    ],
                  ),
                ),
              ),
            ),
        ]
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
              const SnackBar(content: Text("已复制到剪贴板"), duration: Duration(milliseconds: 500)),
            );
          },
          trailing: IconButton(
            icon: const Icon(Icons.delete_outline, size: 20, color: Colors.grey),
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
