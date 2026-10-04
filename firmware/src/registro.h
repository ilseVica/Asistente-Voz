#pragma once

#include <Arduino.h>

// Log por consola con niveles. Nunca imprime secretos (3.4): el host del broker
// y el usuario si, la contrasena no.

enum class NivelLog : uint8_t { Error = 0, Aviso = 1, Info = 2, Depurar = 3 };

void registrar_iniciar(NivelLog nivel);
NivelLog registrar_nivel();
void registrar_escribir(NivelLog nivel, const char *etiqueta, const char *formato, ...);

// Marca de tiempo corta para las lineas de estado. Usa la hora del sistema, que
// configTime deja en UTC, y la convierte con la zona horaria de config.h.
String registrar_hora();

#define log_error(etiqueta, ...) registrar_escribir(NivelLog::Error, etiqueta, __VA_ARGS__)
#define log_aviso(etiqueta, ...) registrar_escribir(NivelLog::Aviso, etiqueta, __VA_ARGS__)
#define log_info(etiqueta, ...) registrar_escribir(NivelLog::Info, etiqueta, __VA_ARGS__)
#define log_depurar(etiqueta, ...) registrar_escribir(NivelLog::Depurar, etiqueta, __VA_ARGS__)

// Construye el id de alerta del plan: MAC[2..7] + "-b" + arranque + "-" + contador.
//   ejemplo: a4cf12-b0007-0042
// El arranque viene de NVS y el contador es interno de alertas.cpp. Ninguno de los
// dos se incrementa al reenviar una alerta pendiente, para que el servidor la
// deduplique por id.
String id_alerta_construir(uint16_t arranque, uint16_t contador);

// Los seis caracteres intermedios del MAC, en minusculas y sin separadores.
String identificador_mac_nodo();
