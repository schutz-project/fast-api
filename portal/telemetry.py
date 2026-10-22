import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

class RetainedFileHandler(RotatingFileHandler):
    """Bounded files, daily rollover, old backup removal on the next write."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.rollover_day=int(Path(self.baseFilename).stat().st_mtime//86400)

    def emit(self, record):
        current=time.time()
        if int(current//86400)>self.rollover_day:
            self.doRollover()
            self.rollover_day=int(current//86400)
        if current-getattr(self,'last_cleanup',0)>3600:
            self.last_cleanup=current
            for number in range(1,self.backupCount+1):
                file=Path(self.baseFilename+'.'+str(number))
                if file.exists() and current-file.stat().st_mtime>7*86400:
                    file.unlink(missing_ok=True)
        super().emit(record)

class Telemetry:
    def __init__(self, settings, metric_readers=None):
        resource=Resource.create({'service.name':'portal-api','deployment.environment.name':settings.environment})
        readers=list(metric_readers or [])
        if settings.otlp_metrics_endpoint:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=settings.otlp_metrics_endpoint,timeout=3),export_interval_millis=30000))
        self.meter_provider=MeterProvider(resource=resource,metric_readers=readers)
        self.tracer_provider=TracerProvider(resource=resource)
        self.tracer=self.tracer_provider.get_tracer('portal')
        meter=self.meter_provider.get_meter('portal')
        self.requests=meter.create_counter('portal.http.requests',unit='{request}')
        self.duration=meter.create_histogram('portal.http.duration',unit='ms')
        self.db_duration=meter.create_histogram('portal.db.duration',unit='ms')
        self.logger=logging.getLogger('portal.events.'+str(id(self)))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate=False
        handler=logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger.addHandler(handler)
        if settings.log_file:
            Path(settings.log_file).parent.mkdir(parents=True,exist_ok=True)
            handler=RetainedFileHandler(settings.log_file,maxBytes=10*1024*1024,backupCount=5,encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(message)s'))
            self.logger.addHandler(handler)

    def record(self,event):
        self.logger.info(json.dumps(event,ensure_ascii=False,separators=(',',':')))
        labels={'http.route':event['route'],'http.response.status_code':event['status_code']}
        self.requests.add(1,labels)
        self.duration.record(event['duration_ms'],labels)
        if event['db_duration_ms'] is not None:
            self.db_duration.record(event['db_duration_ms'],labels)

    def shutdown(self):
        self.meter_provider.shutdown()
        self.tracer_provider.shutdown()
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
