// This is a basic Flutter widget test.
//
// To perform an interaction with a widget in your test, use the WidgetTester
// utility in the flutter_test package. For example, you can send tap and scroll
// gestures. You can also use WidgetTester to find child widgets in the widget
// tree, read text, and verify that the values of widget properties are correct.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:phone2pc_client/file_transfer_page.dart';
import 'package:phone2pc_client/main.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('Phone2PC connection screen smoke test', (
    WidgetTester tester,
  ) async {
    SharedPreferences.setMockInitialValues(<String, Object>{});
    await tester.pumpWidget(const MyApp());
    await tester.pump();
    expect(find.text('智连 Phone2PC'), findsOneWidget);
    expect(find.text('连接'), findsOneWidget);
    expect(find.text('首次连接配对码'), findsOneWidget);
  });

  testWidgets(
    'transfer record supports long press menu and right swipe delete',
    (WidgetTester tester) async {
      const message = '✅ 接收并校验成功: example.pdf';
      SharedPreferences.setMockInitialValues(<String, Object>{
        'transfer_records_v2': <String>[
          jsonEncode(<String, Object?>{
            'id': 'record-1',
            'message': message,
            'created_at': 1,
            'direction': 'receive',
            'file_name': 'example.pdf',
            'file_path': '/storage/emulated/0/Download/Phone2PC/example.pdf',
            'source_uri': null,
          }),
        ],
      });

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: FileTransferPage(
              peerHost: '127.0.0.1',
              onSendJson: (_) async {},
              onSendBinary: (_) async {},
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text(message), findsOneWidget);

      await tester.longPress(find.text(message));
      await tester.pumpAndSettle();
      expect(find.text('打开'), findsOneWidget);
      expect(find.text('打开目录'), findsOneWidget);
      expect(find.text('删除传输记录'), findsOneWidget);

      await tester.tapAt(const Offset(8, 8));
      await tester.pumpAndSettle();
      await tester.drag(find.text(message), const Offset(700, 0));
      await tester.pumpAndSettle();
      expect(find.text(message), findsNothing);

      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pump();
    },
  );
}
