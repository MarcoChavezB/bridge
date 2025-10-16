#!/usr/bin/env python3
"""
usb_printer_bridge.py

Puente simple USB -> PTY (pseudo-serial).
Funciona mejor en Linux/macOS con libusb (pyusb).

Ejemplo:
  sudo python3 usb_printer_bridge.py --vid 0x1234 --pid 0x5678

Si no se indican VID/PID, intenta detectar el primer dispositivo con interface class = 7 (Printer).
"""
import argparse
import os
import pty
import sys
import threading
import time
import usb.core
import usb.util
import select
import signal
import errno

# Ajustes
USB_READ_TIMEOUT = 200  # ms
USB_WRITE_TIMEOUT = 5000  # ms
LOG_PREFIX = "[bridge]"

class USBPrinterBridge:
    def __init__(self, dev):
        self.dev = dev
        self.out_ep = None
        self.in_ep = None
        self.iface = None
        self._should_stop = False
        self._master_fd = None
        self._slave_name = None
        self._orig_kernel_attached = False

    def _find_endpoints(self):
        # Busca interface con clase Printer (7) o usa primera interfaz
        cfg = self.dev.get_active_configuration()
        intf = None
        for interface in cfg:
            if interface.bInterfaceClass == 7:
                intf = interface
                break
        if intf is None:
            intf = cfg[(0,0)]
        self.iface = intf.bInterfaceNumber

        out_ep = None
        in_ep = None
        for ep in intf.endpoints():
            addr = ep.bEndpointAddress
            if usb.util.endpoint_direction(addr) == usb.util.ENDPOINT_OUT:
                out_ep = ep
            elif usb.util.endpoint_direction(addr) == usb.util.ENDPOINT_IN:
                in_ep = ep

        self.out_ep = out_ep
        self.in_ep = in_ep

    def _claim(self):
        try:
            if self.dev.is_kernel_driver_active(self.iface):
                try:
                    self.dev.detach_kernel_driver(self.iface)
                    self._orig_kernel_attached = True
                    print(f"{LOG_PREFIX} detached kernel driver from iface {self.iface}")
                except usb.core.USBError as e:
                    print(f"{LOG_PREFIX} no pude detach kernel driver: {e}")
        except (NotImplementedError, AttributeError):
            pass

        usb.util.claim_interface(self.dev, self.iface)

    def _release(self):
        try:
            usb.util.release_interface(self.dev, self.iface)
        except Exception:
            pass
        if self._orig_kernel_attached:
            try:
                self.dev.attach_kernel_driver(self.iface)
            except Exception:
                pass

    def _create_pty(self):
        master, slave = pty.openpty()
        self._master_fd = master
        self._slave_name = os.ttyname(slave)
        os.close(slave)

    def start(self):
        self._find_endpoints()
        if self.out_ep is None and self.in_ep is None:
            raise RuntimeError("No encontré endpoints IN/OUT en la interfaz USB seleccionada")

        self._claim()
        self._create_pty()

        print(f"{LOG_PREFIX} PTY slave device: {self._slave_name}")
        sys.stdout.flush()

        if self.in_ep is not None:
            t = threading.Thread(target=self._usb_reader_loop, daemon=True)
            t.start()

        try:
            self._pty_writer_loop()
        finally:
            self.stop()

    def stop(self):
        if self._should_stop:
            return
        self._should_stop = True
        print(f"{LOG_PREFIX} stopping...")
        try:
            if self._master_fd:
                os.close(self._master_fd)
        except Exception:
            pass
        try:
            self._release()
        except Exception:
            pass
        print(f"{LOG_PREFIX} stopped")

    def _usb_reader_loop(self):
        bufsize = self.in_ep.wMaxPacketSize if hasattr(self.in_ep, 'wMaxPacketSize') else 512
        while not self._should_stop:
            try:
                data = self.dev.read(self.in_ep.bEndpointAddress, bufsize, timeout=USB_READ_TIMEOUT)
                if data:
                    try:
                        os.write(self._master_fd, bytes(data))
                        print(f"{LOG_PREFIX} USB->PTY {len(data)} bytes")
                    except OSError as e:
                        if e.errno == errno.EBADF:
                            break
            except usb.core.USBError as e:
                pass
            except Exception as e:
                print(f"{LOG_PREFIX} error en usb_reader: {e}")
                break
            time.sleep(0.001)

    def _pty_writer_loop(self):
        master = self._master_fd
        while not self._should_stop:
            rlist, _, _ = select.select([master], [], [], 0.2)
            if master in rlist:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                if self.out_ep is None:
                    print(f"{LOG_PREFIX} app escribió {len(data)} bytes pero no hay endpoint OUT; ignorando")
                    continue
                try:
                    self.dev.write(self.out_ep.bEndpointAddress, data, timeout=USB_WRITE_TIMEOUT)
                    print(f"{LOG_PREFIX} PTY->USB {len(data)} bytes")
                except usb.core.USBError as e:
                    print(f"{LOG_PREFIX} error al escribir USB: {e}")
            time.sleep(0.001)

def find_printer_device(vid=None, pid=None):
    if vid is not None and pid is not None:
        dev = usb.core.find(idVendor=vid, idProduct=pid)
        return dev
    all_devs = usb.core.find(find_all=True)
    for d in all_devs:
        try:
            for cfg in d:
                for intf in cfg:
                    if intf.bInterfaceClass == 7:
                        return d
        except Exception:
            continue
    return None

def signal_handler(sig, frame, bridge=None):
    print(f"\n{LOG_PREFIX} señal recibida, cerrando...")
    if bridge:
        bridge.stop()
    sys.exit(0)

def parse_vid_pid(s):
    if s is None:
        return None
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    try:
        return int(s, 0)
    except Exception:
        return None

def main():
    parser = argparse.ArgumentParser(description="USB -> PTY bridge para impresoras USB")
    parser.add_argument("--vid", help="Vendor ID (ej: 0x1234)", default=None)
    parser.add_argument("--pid", help="Product ID (ej: 0x5678)", default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    vid = parse_vid_pid(args.vid) if args.vid else None
    pid = parse_vid_pid(args.pid) if args.pid else None

    try:
        dev = find_printer_device(vid, pid)
    except usb.core.NoBackendError:
        print(f"{LOG_PREFIX} No se encontró backend de libusb. Instala libusb y pyusb correctamente.")
        sys.exit(1)

    if dev is None:
        print(f"{LOG_PREFIX} No encontré la impresora USB (vid={args.vid} pid={args.pid}).")
        print(f"{LOG_PREFIX} Usa lsusb o pasa --vid/--pid.")
        sys.exit(2)

    try:
        dev.set_configuration()
    except Exception:
        pass

    bridge = USBPrinterBridge(dev)

    signal.signal(signal.SIGINT, lambda s,f: signal_handler(s,f,bridge=bridge))
    signal.signal(signal.SIGTERM, lambda s,f: signal_handler(s,f,bridge=bridge))

    try:
        bridge.start()
    except Exception as e:
        print(f"{LOG_PREFIX} error: {e}")
        bridge.stop()
        sys.exit(3)

if __name__ == "__main__":
    main()
