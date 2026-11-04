"""El nivel de agua (Fase del bote): episodios de BAJO y LLENO.

El firmware publica `agua_pct` en `casa/sala/sensores`. Este modulo lleva la
cuenta de los episodios: abre con una lectura fuera del umbral (la histeresis
evita el parpadeo) y encola una alerta a la familia (Telegram + panel) una sola
vez por episodio. El asistente NO lo dice en voz alta.

El estado del episodio se persiste en SQLite (tabla `configuracion`, clave
`agua_episodio`) para que un reinicio del orquestador no repita la alerta.
"""

import json
import logging
import threading
import time

import config

registro = logging.getLogger("agua")

CLAVE_EPISODIO = "agua_episodio"


class EstadoAgua:
    def __init__(self, base, ahora=None):
        self.base = base
        self._cerrojo = threading.Lock()
        if ahora is None:
            ahora = int(time.time())
        crudo = None
        try:
            crudo = base._config_leer(CLAVE_EPISODIO)
        except Exception:  # noqa: BLE001
            crudo = None
        self.tipo_actual = None   # "bajo" | "lleno" | None
        self.inicio_ts = None
        if crudo:
            partes = str(crudo).split(":", 1)
            if len(partes) == 2:
                try:
                    self.tipo_actual = partes[0]
                    self.inicio_ts = int(partes[1])
                except ValueError:
                    self.tipo_actual = None
                    self.inicio_ts = None
        self.consec_bajo = 0
        self.consec_lleno = 0
        self.ultimo_pct = None

    def _persistir(self) -> None:
        if self.tipo_actual and self.inicio_ts is not None:
            self.base._config_escribir(CLAVE_EPISODIO,
                                       "%s:%d" % (self.tipo_actual, self.inicio_ts))
        else:
            self.base._config_escribir(CLAVE_EPISODIO, "")

    def registrar_sensores(self, cuerpo, ahora=None, publicar=None) -> None:
        try:
            carga = json.loads(cuerpo)
        except (TypeError, ValueError):
            return
        if not isinstance(carga, dict):
            return
        if "agua_pct" not in carga:
            return
        pct = carga.get("agua_pct")
        if pct is None:
            return
        try:
            pct = int(pct)
        except (TypeError, ValueError):
            return
        if ahora is None:
            ahora = int(time.time())
        with self._cerrojo:
            self.ultimo_pct = pct
            if pct < config.AGUA_BAJO_ABRE_PCT:
                self.consec_bajo += 1
                self.consec_lleno = 0
            else:
                self.consec_bajo = 0
            if pct > config.AGUA_LLENO_ABRE_PCT:
                self.consec_lleno += 1
                self.consec_bajo = 0 if pct >= config.AGUA_BAJO_ABRE_PCT else self.consec_bajo
            else:
                self.consec_lleno = 0

            # Apertura
            if (self.tipo_actual is None
                    and self.consec_bajo >= config.AGUA_LECTURAS_PARA_ABRIR):
                self.tipo_actual = "bajo"
                self.inicio_ts = ahora
                self._persistir()
                self._alertar(publicar, "bajo", pct, ahora)
                self.consec_bajo = 0
                return
            if (self.tipo_actual is None
                    and self.consec_lleno >= config.AGUA_LECTURAS_PARA_ABRIR):
                self.tipo_actual = "lleno"
                self.inicio_ts = ahora
                self._persistir()
                self._alertar(publicar, "lleno", pct, ahora)
                self.consec_lleno = 0
                return

            # Cierre por histeresis
            if self.tipo_actual == "bajo" and pct > config.AGUA_BAJO_CIERRA_PCT:
                self.tipo_actual = None
                self.inicio_ts = None
                self._persistir()
                registro.info("episodio de agua BAJO cerrado (pct=%d)", pct)
            elif self.tipo_actual == "lleno" and pct < config.AGUA_LLENO_CIERRA_PCT:
                self.tipo_actual = None
                self.inicio_ts = None
                self._persistir()
                registro.info("episodio de agua LLENO cerrado (pct=%d)", pct)

    def _alertar(self, publicar, tipo, pct, ahora) -> None:
        id_alerta = "srv-agua-%s-%d" % (tipo, self.inicio_ts)
        carga = {"id": id_alerta, "tipo": tipo, "agua_pct": pct}
        texto = json.dumps(carga, separators=(",", ":"))
        if not self.base.registrar_id(id_alerta, "agua"):
            registro.info("el agua %s ya estaba registrada; no se repite", id_alerta)
            return
        if not self.base.insertar_alerta(id_alerta, "agua", texto):
            registro.info("el agua %s ya estaba en la cola", id_alerta)
            return
        if publicar is not None:
            try:
                publicar(config.TOPICO_ALERTA_AGUA, carga, qos=1, retain=False)
            except Exception as error:  # noqa: BLE001
                registro.error("no se pudo publicar alerta de agua en MQTT: %s", error)
        destinatarios = self.base.contar_familiares_con_telegram()
        registro.warning("ALERTA de agua %s: %d %% (tipo %s); encolada para %d "
                         "familiar(es)", id_alerta, pct, tipo, destinatarios)
