package com.uavusv.platform.module.sensor.integration;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.*;
import org.springframework.web.socket.config.annotation.*;
import org.springframework.web.socket.handler.BinaryWebSocketHandler;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.io.ByteArrayOutputStream;
import java.time.Duration;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

@Configuration
@EnableWebSocket
public class LidarStreamConfig extends BinaryWebSocketHandler implements WebSocketConfigurer {
 private final ConcurrentHashMap<String,WebSocketSession> sessions=new ConcurrentHashMap<>();
 private final ScheduledExecutorService executor=Executors.newScheduledThreadPool(2);
 private final AtomicReference<byte[]> latest=new AtomicReference<>();
 private final AtomicBoolean connecting=new AtomicBoolean();
 private final HttpClient client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
 private volatile WebSocket upstream;
 private volatile boolean closed;
 private byte[] sent;
 @Value("${LIDAR_STREAM_WS_URL:}") private String endpoint;
 public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
  registry.addHandler(this,"/api/lidar/stream").setAllowedOriginPatterns("http://localhost:*","http://127.0.0.1:*");
 }
 @Override public void afterConnectionEstablished(WebSocketSession session) throws Exception {
  if(session.getPrincipal()==null){session.close(CloseStatus.NOT_ACCEPTABLE);return;}
  sessions.put(session.getId(),new ConcurrentWebSocketSessionDecorator(session,1000,131072));
 }
 @Override public void afterConnectionClosed(WebSocketSession s,CloseStatus c){sessions.remove(s.getId());}
 @PostConstruct public void start(){
  executor.scheduleWithFixedDelay(this::connect,0,2,TimeUnit.SECONDS);
  executor.scheduleWithFixedDelay(()->{byte[] frame=latest.get();if(frame==null||frame==sent)return;sent=frame;
   sessions.forEach((id,s)->{try{if(s.isOpen())s.sendMessage(new BinaryMessage(frame));else sessions.remove(id);}catch(Exception e){sessions.remove(id);try{s.close();}catch(Exception ignored){}}});
  },0,5,TimeUnit.MILLISECONDS);
 }
 private void connect(){
  if(closed||endpoint.isBlank()||upstream!=null||!connecting.compareAndSet(false,true))return;
  client.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(3)).buildAsync(URI.create(endpoint),new WebSocket.Listener(){
   final ByteArrayOutputStream buffer=new ByteArrayOutputStream();
   public void onOpen(WebSocket ws){upstream=ws;ws.request(1);}
   public CompletionStage<?> onBinary(WebSocket ws,ByteBuffer data,boolean last){
    if(buffer.size()+data.remaining()>131072){buffer.reset();ws.abort();upstream=null;return null;}
    byte[] part=new byte[data.remaining()];data.get(part);buffer.writeBytes(part);
    if(last){byte[] frame=buffer.toByteArray();buffer.reset();if(frame.length>=32&&frame[0]=='P'&&frame[1]=='C'&&frame[2]=='D'&&frame[3]=='1')latest.set(frame);}
    ws.request(1);return null;
   }
   public CompletionStage<?> onClose(WebSocket w,int code,String reason){upstream=null;latest.set(null);return null;}
   public void onError(WebSocket w,Throwable error){upstream=null;latest.set(null);}
  }).whenComplete((ws,error)->connecting.set(false));
 }
 @PreDestroy public void stop(){closed=true;executor.shutdownNow();if(upstream!=null)upstream.abort();sessions.values().forEach(s->{try{s.close();}catch(Exception ignored){}});}
}
