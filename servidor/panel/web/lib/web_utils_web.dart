import 'dart:html';

Future<void> borrarFragmento() async {
  window.history.replaceState(null, '', Uri.base.path);
}
