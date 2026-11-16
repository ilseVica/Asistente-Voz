"""Saludo diario: Lucía pregunta «¿Cómo amaneció?» una vez al día."""

import json
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import textos
import voz

registro = logging.getLogger("saludo_diario")

SALA_CID = getattr(config, "SATELITE_VIDEO_CLIENT_ID", "satelite-sala") or "satelite-sala"


def _config() -> dict:
    d = getattr(config, "SALUDO_DIARIO", None)
    if isinstance(d, dict):
        return {
            "activo": d.get("activo", getattr(config, "SALUDO_DIARIO_ACTIVO", False)),
            "hora": d.get("hora", getattr(config, "SALUDO_DIARIO_HORA", "09:00")),
            "zona": d.get("zona", getattr(config, "SALUDO_DIARIO_ZONA", "America/Mazatlan")),
            "reintento_min": d.get("reintento_min", getattr(config, "SALUDO_DIARIO_REINTENTO_MIN", 10)),
            "avisar_si_bien": d.get("avisar_si_bien", getattr(config, "SALUDO_DIARIO_AVISAR_SI_BIEN", True)),
        }
    return {
        "activo": getattr(config, "SALUDO_DIARIO_ACTIVO", False),
        "hora": getattr(config, "SALUDO_DIARIO_HORA", "09:00"),
        "zona": getattr(config, "SALUDO_DIARIO_ZONA", "America/Mazatlan"),
        "reintento_min": getattr(config, "SALUDO_DIARIO_REINTENTO_MIN", 10),
        "avisar_si_bien": getattr(config, "SALUDO_DIARIO_AVISAR_SI_BIEN", True),
    }


def _normalizar(texto) -> str:
    try:
        return voz.normalizar(texto)
    except Exception:  # noqa: BLE001
        return str(texto or "").strip().lower()


_BIEN = {"bien", "muy bien", "bien gracias", "muy bien gracias", "estoy bien", "me siento bien"}
_REGULAR = {"mas o menos", "más o menos", "regular", "no muy bien"}
_MAL = {"mal", "muy mal", "me siento mal", "no me siento bien", "no estoy bien", "me siento muy mal"}


class Estado:
    def __init__(self, base=None, satelites=None, publicar=None, remitente=None,
                 ahora=None):
        self.base = base
        self.satelites = satelites
        self.publicar = publicar
        self.remitente = remitente
        self._zona = ZoneInfo(_config()["zona"])
        if ahora is None:
            ahora = time.time()
        self._ahora0 = ahora
        self._disparado = False
        self._fecha_disparo = None
        self._hora_disparo = None
        self._adelantado = False
        self._fase = 0  # 0 nada, 1 preguntado, 2 repetido
        self._ventana_hasta = 0.0
        self._reintento_en = None
        self._segundo_limite = None
        self._no_entendido_usado = False

    def _cfg(self) -> dict:
        return _config()

    def _zona_actual(self) -> ZoneInfo:
        return ZoneInfo(self._cfg()["zona"])

    def _sala_conectada(self) -> bool:
        s = self.satelites
        cid = SALA_CID
        if s is None:
            return False
        for nombre in ("conectado", "esta_conectado", "is_connected"):
            fn = getattr(s, nombre, None)
            if callable(fn):
                try:
                    return bool(fn(cid))
                except TypeError:
                    try:
                        return bool(fn())
                    except Exception:  # noqa: BLE001
                        pass
        conectados = getattr(s, "conectados", None)
        if callable(conectados):
            try:
                return cid in (conectados() or [])
            except Exception:  # noqa: BLE001
                pass
        hay = getattr(s, "hay_conectado", None)
        if callable(hay):
            try:
                return bool(hay())
            except Exception:  # noqa: BLE001
                pass
        return False

    def _hay_alerta_abierta(self) -> bool:
        if self.base is not None and hasattr(self.base, "hay_alerta_abierta"):
            try:
                return bool(self.base.hay_alerta_abierta())
            except Exception:  # noqa: BLE001
                pass
        return False

    def _fecha_str(self, ahora) -> str:
        return datetime.fromtimestamp(ahora, self._zona_actual()).date().isoformat()

    def _hora_str(self, ahora) -> str:
        dt = datetime.fromtimestamp(ahora, self._zona_actual())
        return "%02d:%02d" % (dt.hour, dt.minute)

    def _epoch_hora_hoy(self, ahora, hora_str) -> float:
        dt = datetime.fromtimestamp(ahora, self._zona_actual())
        hh, mm = (hora_str or "09:00").split(":")
        dt = dt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        return dt.timestamp()

    def _publicar(self, topico, carga, qos=1):
        if self.publicar is None:
            return
        try:
            self.publicar(topico, carga, qos=qos)
        except Exception:  # noqa: BLE001
            pass

    def _abrir_alerta_bienestar(self, ahora, texto) -> None:
        if self.base is None:
            return
        id_alerta = "srv-bienestar-%d" % int(ahora)
        carga = {"id": id_alerta, "tipo": "bienestar", "texto": texto}
        try:
            if hasattr(self.base, "registrar_id"):
                self.base.registrar_id(id_alerta, "bienestar")
            if hasattr(self.base, "insertar_alerta"):
                self.base.insertar_alerta(id_alerta, "bienestar",
                                         json.dumps(carga, separators=(",", ":")))
        except Exception:  # noqa: BLE001
            pass
        try:
            self._publicar(getattr(config, "TOPICO_ALERTA_BIENESTAR",
                                   "casa/sala/alerta/bienestar"),
                           carga, qos=1)
        except Exception:  # noqa: BLE001
            pass

    def _cerrar_ventana(self) -> None:
        self._fase = 0
        self._ventana_hasta = 0.0
        self._reintento_en = None
        self._segundo_limite = None
        self._no_entendido_usado = False

    def disparar(self, ahora=None, manual=False) -> bool:
        cfg = self._cfg()
        if ahora is None:
            ahora = time.time()
        fecha = self._fecha_str(ahora)
        # ya hecho hoy (salvo manual)
        if (not manual) and (self._disparado and self._fecha_disparo == fecha):
            return False
        if (not manual) and self.base is not None and hasattr(self.base, "saludo_diario_hecho"):
            if self.base.saludo_diario_hecho(fecha):
                self._disparado = True
                self._fecha_disparo = fecha
                return False
        if (not manual) and not cfg.get("activo", False):
            return False
        if (not manual) and self._hay_alerta_abierta():
            registro.info("saludo diario: saltado por alerta abierta")
            return False
        if not self._sala_conectada():
            # aviso (no alerta)
            if self.base is not None and hasattr(self.base, "crear_aviso_conversacion"):
                try:
                    self.base.crear_aviso_conversacion(
                        "La sala", "0", "panel",
                        textos.SALUDO_DIARIO_SIN_MICROFONO)
                except Exception:  # noqa: BLE001
                    pass
            self._disparado = True
            self._fecha_disparo = fecha
            return False
        # hablar
        self._publicar(getattr(config, "TOPICO_SATELITE_DECIR", "casa/sala/satelite/decir"),
                       {"texto": textos.SALUDO_DIARIO_PREGUNTA, "tipo": "saludo",
                        "ts": int(ahora), "destino": SALA_CID,
                        "escuchar_al_terminar": True}, qos=1)
        self._disparado = True
        self._fecha_disparo = fecha
        self._hora_disparo = ahora
        self._fase = 1
        self._ventana_hasta = ahora + int(cfg.get("reintento_min", 10)) * 60
        self._reintento_en = ahora + int(cfg.get("reintento_min", 10)) * 60
        self._segundo_limite = None
        self._no_entendido_usado = False
        self._adelantado = False
        return True

    def verificar(self, ahora=None) -> None:
        cfg = self._cfg()
        if not cfg.get("activo", False):
            return
        if ahora is None:
            ahora = time.time()
        fecha = self._fecha_str(ahora)
        if not (self._disparado and self._fecha_disparo == fecha):
            sched = self._epoch_hora_hoy(ahora, cfg.get("hora", "09:00"))
            if ahora >= sched:
                self.disparar(ahora=ahora, manual=False)
        # reintentos
        if self._fase == 1 and self._reintento_en is not None and ahora >= self._reintento_en:
            self._publicar(getattr(config, "TOPICO_SATELITE_DECIR", "casa/sala/satelite/decir"),
                           {"texto": textos.SALUDO_DIARIO_REINTENTO, "tipo": "saludo",
                            "ts": int(ahora), "destino": SALA_CID,
                            "escuchar_al_terminar": True}, qos=1)
            self._fase = 2
            self._segundo_limite = ahora + int(cfg.get("reintento_min", 10)) * 60
            self._reintento_en = None
        elif self._fase == 2 and self._segundo_limite is not None and ahora >= self._segundo_limite:
            h1 = self._hora_str(self._hora_disparo) if self._hora_disparo else ""
            h2 = self._hora_str(ahora)
            texto_alerta = "Lucía saludó a las %s y a las %s y no tuvo respuesta." % (h1, h2)
            self._abrir_alerta_bienestar(ahora, texto_alerta)
            if self.base is not None and hasattr(self.base, "registrar_saludo_diario"):
                try:
                    self.base.registrar_saludo_diario(fecha, "sin respuesta", ahora)
                except Exception:  # noqa: BLE001
                    pass
            self._cerrar_ventana()

    def adelantar(self, ahora=None) -> bool:
        """Llamado cuando el usuario dice 'buenos días' o 'ya me levanté' antes."""
        if ahora is None:
            ahora = time.time()
        cfg = self._cfg()
        if not cfg.get("activo", False):
            return False
        fecha = self._fecha_str(ahora)
        if self._disparado and self._fecha_disparo == fecha:
            return False
        if self.base is not None and hasattr(self.base, "saludo_diario_hecho"):
            if self.base.saludo_diario_hecho(fecha):
                return False
        # disparar en modo adelantado: pregunta directamente (sin repetir buenos dias)
        if self._hay_alerta_abierta():
            registro.info("saludo diario: saltado por alerta abierta")
            return False
        if not self._sala_conectada():
            return False
        self._publicar(getattr(config, "TOPICO_SATELITE_DECIR", "casa/sala/satelite/decir"),
                       {"texto": textos.SALUDO_DIARIO_PREGUNTA, "tipo": "saludo",
                        "ts": int(ahora), "destino": SALA_CID,
                        "escuchar_al_terminar": True}, qos=1)
        self._disparado = True
        self._fecha_disparo = fecha
        self._hora_disparo = ahora
        self._adelantado = True
        self._fase = 1
        self._ventana_hasta = ahora + int(cfg.get("reintento_min", 10)) * 60
        self._reintento_en = ahora + int(cfg.get("reintento_min", 10)) * 60
        self._segundo_limite = None
        self._no_entendido_usado = False
        return True

    def responder(self, texto, ahora=None) -> bool:
        if ahora is None:
            ahora = time.time()
        if self._fase not in (1, 2):
            return False
        if self._ventana_hasta and ahora > self._ventana_hasta + 300:
            return False
        norm = _normalizar(texto)
        cfg = self._cfg()
        fecha = self._fecha_str(ahora)
        if norm in _BIEN:
            self._publicar(getattr(config, "TOPICO_SATELITE_DECIR", "casa/sala/satelite/decir"),
                           {"texto": textos.SALUDO_DIARIO_BIEN, "tipo": "saludo",
                            "ts": int(ahora), "destino": SALA_CID}, qos=1)
            if not self._adelantado:
                try:
                    voz._buenos_dias(self.publicar, None, self.base)
                except Exception:  # noqa: BLE001
                    pass
            if cfg.get("avisar_si_bien", True):
                # Telegram sin sonido
                if self.base is not None:
                    try:
                        if hasattr(self.base, "crear_respuesta_conversacion"):
                            self.base.crear_respuesta_conversacion(
                                textos.SALUDO_DIARIO_AVISO_BIEN)
                    except Exception:  # noqa: BLE001
                        pass
                if self.remitente is not None and self.base is not None:
                    for fam in self.base.familiares_con_telegram():
                        try:
                            self.remitente.enviar(
                                textos.SALUDO_DIARIO_AVISO_BIEN,
                                chat_id=str(fam["chat_id"]),
                                disable_notification=True)
                        except Exception:  # noqa: BLE001
                            pass
            if self.base is not None and hasattr(self.base, "registrar_saludo_diario"):
                try:
                    self.base.registrar_saludo_diario(fecha, "bien", ahora)
                except Exception:  # noqa: BLE001
                    pass
            self._cerrar_ventana()
            return True
        if norm in _REGULAR:
            self._publicar(getattr(config, "TOPICO_SATELITE_DECIR", "casa/sala/satelite/decir"),
                           {"texto": textos.SALUDO_DIARIO_REGULAR, "tipo": "saludo",
                            "ts": int(ahora), "destino": SALA_CID}, qos=1)
            if self.base is not None and hasattr(self.base, "crear_respuesta_conversacion"):
                try:
                    self.base.crear_respuesta_conversacion(
                        textos.SALUDO_DIARIO_AVISO_REGULAR)
                except Exception:  # noqa: BLE001
                    pass
            if self.base is not None and hasattr(self.base, "registrar_saludo_diario"):
                try:
                    self.base.registrar_saludo_diario(fecha, "regular", ahora)
                except Exception:  # noqa: BLE001
                    pass
            self._cerrar_ventana()
            return True
        if norm in _MAL:
            self._publicar(getattr(config, "TOPICO_SATELITE_DECIR", "casa/sala/satelite/decir"),
                           {"texto": textos.SALUDO_DIARIO_MAL, "tipo": "saludo",
                            "ts": int(ahora), "destino": SALA_CID}, qos=1)
            self._abrir_alerta_bienestar(ahora, "La sala dice que se siente mal.")
            if self.base is not None and hasattr(self.base, "registrar_saludo_diario"):
                try:
                    self.base.registrar_saludo_diario(fecha, "mal", ahora)
                except Exception:  # noqa: BLE001
                    pass
            self._cerrar_ventana()
            return True
        if norm in ("ayuda",) or norm in voz._frase_a_intencion and voz._frase_a_intencion.get(norm) == "AYUDA":
            self._cerrar_ventana()
            # delegar a voz
            try:
                voz.procesar(json.dumps({"ts": int(ahora), "texto": "ayuda",
                                         "frase_id": "saludo-ayuda"}), self.publicar, self.base)
            except Exception:  # noqa: BLE001
                pass
            return True
        # otra cosa
        if not self._no_entendido_usado:
            self._publicar(getattr(config, "TOPICO_SATELITE_DECIR", "casa/sala/satelite/decir"),
                           {"texto": textos.SALUDO_DIARIO_NO_ENTENDI, "tipo": "saludo",
                            "ts": int(ahora), "destino": SALA_CID,
                            "escuchar_al_terminar": True}, qos=1)
            self._no_entendido_usado = True
            return True
        self._cerrar_ventana()
        return False

    def ventana_abierta(self) -> bool:
        return self._fase in (1, 2)


# Alias
Saludo = Estado
SaludoDiario = Estado
