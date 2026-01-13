import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:share_plus/share_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:crypto/crypto.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:open_file/open_file.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';

class FileTransferPage extends StatefulWidget {
  final Function(Map<String, dynamic>) onSendJson;
  final Function(List<int>) onSendBinary;

  const FileTransferPage({
    super.key, 
    required this.onSendJson,
    required this.onSendBinary,
  });

  @override
  State<FileTransferPage> createState() => FileTransferPageState();
}

class FileTransferPageState extends State<FileTransferPage> {
  // 接收文件状态
  final Map<String, dynamic> _receivingFiles = {}; 
  String? _currentReceiveId; // v5.0: Single active file assumption for binary routing
  int _bytesSinceLastAck = 0;
  final int _ackThreshold = 2 * 1024 * 1024; // 2MB Window

  // 发送文件状态
  Completer<void>? _ackCompleter; 
  
  List<String> _transferLogs = [];

  @override
  void initState() {
      super.initState();
      _loadLogs();
  }

  Future<void> _loadLogs() async {
      final prefs = await SharedPreferences.getInstance();
      setState(() {
          _transferLogs = prefs.getStringList('transfer_logs') ?? [];
      });
  }

  Future<void> _saveLogs() async {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList('transfer_logs', _transferLogs);
  }
  
  void _clearLogs() async {
      setState(() => _transferLogs.clear());
      _saveLogs();
  } 

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Action Bar
        Container(
          padding: const EdgeInsets.all(16),
          color: Colors.blue.shade50,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              ElevatedButton.icon(
                onPressed: _pickAndSendFiles, 
                icon: const Icon(Icons.upload_file), 
                label: const Text("发送文件")
              ),
              ElevatedButton.icon(
                onPressed: _openReceiveFolder, 
                icon: const Icon(Icons.folder_open), 
                label: const Text("接收目录")
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        // Logs
        Expanded(
          child: _transferLogs.isEmpty 
          ? const Center(child: Text("暂无传输记录")) 
          : ListView.builder(
            itemCount: _transferLogs.length,
            itemBuilder: (ctx, i) {
                final msg = _transferLogs[_transferLogs.length - 1 - i];
                return _buildLogItem(msg, i);
            },
          ),
        ),
        if (_transferLogs.isNotEmpty)
            TextButton(onPressed: _clearLogs, child: const Text("清空记录", style: TextStyle(color: Colors.grey))),
      ],
    );
  }

  // --- Sending Logic (Binary + Flow Control) ---
  Future<void> _pickAndSendFiles() async {
    FilePickerResult? result = await FilePicker.platform.pickFiles(allowMultiple: true);
    if (result != null) {
      for (var f in result.files) {
        if (f.path != null) {
          _log("📤 准备发送: ${f.name} (${_formatSize(f.size)})");
          await _sendFileWorker(File(f.path!), f.name, f.size); // AWAIT each file
        }
      }
    }
  }

  Future<void> _sendFileWorker(File file, String name, int size) async {
    try {
      final fileId = DateTime.now().millisecondsSinceEpoch.toString();
      const chunkSize = 64 * 1024; // 64KB chunks (matches PC)
      
      // 1. Offer
      widget.onSendJson({
        "type": "FILE_OFFER",
        "file_id": fileId,
        "name": name,
        "size": size
      });
      
      // Small delay to let server prepare
      await Future.delayed(const Duration(milliseconds: 200));
      
      final raf = await file.open(mode: FileMode.read);
      
      // 2. Binary Loop with simple throttling
      while (true) {
         final chunk = await raf.read(chunkSize);
         if (chunk.isEmpty) break;
         
         widget.onSendBinary(chunk);
         
         // Throttle: 1ms per 64KB ≈ 64MB/s max (matches PC)
         await Future.delayed(const Duration(milliseconds: 1));
      } 
      
      await raf.close();
      _log("✅ 发送成功: $name");
      
    } catch (e) {
      _log("❌ 发送失败: $name, $e");
    }
  }

  // --- Receiving Logic ---

  // 1. JSON Signaling
  Future<void> handleFileMessage(Map<String, dynamic> data) async {
    final type = data['type'];
    final fileId = data['file_id'];

    if (type == 'FILE_OFFER') {
        if (_currentReceiveId != null) {
            _log("⚠️ 忙碌中，忽略新文件: ${data['name']}");
            return;
        }
        final name = data['name'];
        final size = data['size'];
        await _startReceive(fileId, name, size);
        
    } else if (type == 'ACK') {
        // Unblock sender
        if (_ackCompleter != null && !_ackCompleter!.isCompleted) {
            _ackCompleter!.complete();
        }
    }
  }
  
  // 2. Binary Data
  Future<void> handleBinaryMessage(List<int> bytes) async {
      if (_currentReceiveId == null) return;
      await _writeChunk(_currentReceiveId!, bytes);
  }

  Future<void> _startReceive(String fileId, String name, int size) async {
    try {
        final downloadDir = Directory("/storage/emulated/0/Download");
        final baseDir = downloadDir.existsSync() ? downloadDir : await getExternalStorageDirectory(); 
        
        final saveDir = Directory("${baseDir!.path}/Phone2PC");
        if (!await saveDir.exists()) {
            await saveDir.create(recursive: true);
        }
        
        String path = "${saveDir.path}/$name";
        int counter = 1;
        while (await File(path).exists()) {
             final dotIndex = name.lastIndexOf('.');
             final base = dotIndex == -1 ? name : name.substring(0, dotIndex);
             final ext = dotIndex == -1 ? "" : name.substring(dotIndex);
             path = "${saveDir.path}/${base}_$counter$ext";
             counter++;
        }

        final file = File(path);
        final sink = file.openWrite(); 
        
        setState(() {
            _receivingFiles[fileId] = {
                "sink": sink,
                "path": path,
                "name": name,
                "size": size,
                "received": 0
            };
            _currentReceiveId = fileId;
            _bytesSinceLastAck = 0;
        });
        
        _log("⬇️ 开始接收: $name");
        
    } catch (e) {
        _log("❌ 创建文件失败: $e");
    }
  }

  Future<void> _writeChunk(String fileId, List<int> bytes) async {
      final info = _receivingFiles[fileId];
      if (info == null) return;
      
      try {
          final IOSink sink = info['sink'];
          sink.add(bytes);
          
          info['received'] += bytes.length;
          // Flow Control: Removed for v5.0.7 (Simple Throttling)
          // PC sends with 1ms delay, we just write.
          // if (_bytesSinceLastAck >= _ackThreshold) { ... }
          
          // Completion Check
          if (info['received'] >= info['size']) {
              await sink.flush();
              await sink.close();
              
              // Final ACK
              widget.onSendJson({"type": "ACK", "file_id": fileId, "received": info['received']});
              
              final name = info['name'];
              final path = info['path'];
              
              setState(() {
                  _receivingFiles.remove(fileId);
                  _currentReceiveId = null;
              });
              
              _log("✅ 接收成功: $name");
              ScaffoldMessenger.of(context).showSnackBar(
                 SnackBar(content: Text("已保存: $name"), action: SnackBarAction(label: "打开", onPressed: () => _safeOpenFile(path)))
              );
          }
          
      } catch (e) {
          _log("❌ 写入出错: $e");
          _currentReceiveId = null; 
      }
  }

  void _log(String msg) {
    if (mounted) {
        setState(() {
            _transferLogs.add(msg);
        });
        _saveLogs();
    }
  }
  
  String _formatSize(int bytes) {
    if (bytes < 1024) return "$bytes B";
    if (bytes < 1024 * 1024) return "${(bytes / 1024).toStringAsFixed(1)} KB";
    return "${(bytes / 1024 / 1024).toStringAsFixed(1)} MB";
  }

  Future<void> _safeOpenFile(String path) async {
      final result = await OpenFile.open(path);
      if (result.type != ResultType.done) {
          ScaffoldMessenger.of(context).showSnackBar(
             const SnackBar(content: Text("尝试调用系统分享打开..."), duration: Duration(seconds: 1))
          );
          Share.shareXFiles([XFile(path)]); 
      }
  }
  
  void _openReceiveFolder() async {
      final path = "/storage/emulated/0/Download/Phone2PC";
      await OpenFile.open(path);
  }

  Widget _buildLogItem(String msg, int index) {
      String displayMsg = msg;
      return ListTile(
        dense: true,
        leading: Icon(
            msg.contains("✅") ? Icons.check_circle : 
            msg.contains("❌") ? Icons.error : 
            msg.contains("📤") ? Icons.upload : Icons.download, 
            size: 18,
            color: msg.contains("✅") ? Colors.green : Colors.grey
        ),
        title: Text(displayMsg),
        onTap: () {},
      );
  }
}
