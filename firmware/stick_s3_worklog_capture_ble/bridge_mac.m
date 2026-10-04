#import <CoreBluetooth/CoreBluetooth.h>
#import <Foundation/Foundation.h>

static NSString *const ServiceUUID = @"74f71000-7d3a-4a6f-9b5b-3ca6e4313f52";
static NSString *const CaptureUUID = @"74f71001-7d3a-4a6f-9b5b-3ca6e4313f52";

static NSString *ReadAuthToken(NSString *path) {
    NSString *environmentToken = NSProcessInfo.processInfo.environment[@"EOS_AUTH_TOKEN"];
    if (environmentToken.length > 0) return environmentToken;

    NSString *contents = [NSString stringWithContentsOfFile:path
                                                   encoding:NSUTF8StringEncoding
                                                      error:nil];
    if (!contents) return @"";

    BOOL inNetworkSection = NO;
    NSCharacterSet *space = NSCharacterSet.whitespaceCharacterSet;
    for (NSString *rawLine in [contents componentsSeparatedByCharactersInSet:
                               NSCharacterSet.newlineCharacterSet]) {
        NSString *line = [rawLine stringByTrimmingCharactersInSet:space];
        if ([line hasPrefix:@"["]) {
            inNetworkSection = [line isEqualToString:@"[network]"];
            continue;
        }
        if (!inNetworkSection || ![line hasPrefix:@"auth_token"]) continue;
        NSRange equals = [line rangeOfString:@"="];
        if (equals.location == NSNotFound) continue;
        NSString *value = [[line substringFromIndex:equals.location + 1]
                           stringByTrimmingCharactersInSet:space];
        if ([value hasPrefix:@"\""] && [value hasSuffix:@"\""] && value.length >= 2) {
            return [value substringWithRange:NSMakeRange(1, value.length - 2)];
        }
    }
    return @"";
}

@interface WorklogBridge : NSObject <CBCentralManagerDelegate, CBPeripheralDelegate>
@property(nonatomic, strong) CBCentralManager *central;
@property(nonatomic, strong) CBPeripheral *peripheral;
@property(nonatomic, strong) CBCharacteristic *characteristic;
@property(nonatomic, copy) NSString *daemon;
@property(nonatomic, copy) NSString *token;
@property(nonatomic, copy) NSString *lastEvent;
@end

@implementation WorklogBridge

- (instancetype)initWithDaemon:(NSString *)daemon config:(NSString *)config {
    self = [super init];
    if (self) {
        while ([daemon hasSuffix:@"/"]) daemon = [daemon substringToIndex:daemon.length - 1];
        _daemon = [daemon copy];
        _token = ReadAuthToken(config);
        _lastEvent = @"";
        _central = [[CBCentralManager alloc] initWithDelegate:self queue:dispatch_get_main_queue()];
        NSLog(@"Worklog BLE bridge started. Waiting for EOS Worklog Button...");
    }
    return self;
}

- (void)centralManagerDidUpdateState:(CBCentralManager *)central {
    switch (central.state) {
        case CBManagerStatePoweredOn:
            [self scan];
            break;
        case CBManagerStateUnauthorized:
            NSLog(@"Bluetooth permission denied. Enable it for Terminal in System Settings > Privacy & Security > Bluetooth.");
            break;
        case CBManagerStatePoweredOff:
            NSLog(@"Bluetooth is off on this Mac.");
            break;
        default:
            break;
    }
}

- (void)scan {
    NSLog(@"Scanning for the StickS3...");
    // macOS can omit a custom 128-bit service from its filtered scan results
    // until it has seen the peripheral once. Scan broadly, then enforce our
    // service UUID or exact local name before connecting.
    [self.central scanForPeripheralsWithServices:nil
                                         options:@{CBCentralManagerScanOptionAllowDuplicatesKey: @NO}];
}

- (void)centralManager:(CBCentralManager *)central
 didDiscoverPeripheral:(CBPeripheral *)peripheral
     advertisementData:(NSDictionary<NSString *, id> *)advertisementData
                  RSSI:(NSNumber *)RSSI {
    NSArray<CBUUID *> *services = advertisementData[CBAdvertisementDataServiceUUIDsKey] ?: @[];
    NSString *localName = advertisementData[CBAdvertisementDataLocalNameKey] ?: peripheral.name;
    BOOL serviceMatches = [services containsObject:[CBUUID UUIDWithString:ServiceUUID]];
    BOOL nameMatches = [localName isEqualToString:@"EOS Worklog Button"];
    if (!serviceMatches && !nameMatches) return;

    self.peripheral = peripheral;
    peripheral.delegate = self;
    [central stopScan];
    NSLog(@"Found %@. Connecting...", peripheral.name ?: @"EOS Worklog Button");
    [central connectPeripheral:peripheral options:nil];
}

- (void)centralManager:(CBCentralManager *)central didConnectPeripheral:(CBPeripheral *)peripheral {
    NSLog(@"Connected. Tap the StickS3 to capture.");
    [peripheral discoverServices:@[[CBUUID UUIDWithString:ServiceUUID]]];
}

- (void)centralManager:(CBCentralManager *)central
 didFailToConnectPeripheral:(CBPeripheral *)peripheral
                  error:(NSError *)error {
    NSLog(@"Connection failed: %@", error.localizedDescription ?: @"unknown error");
    self.peripheral = nil;
    [self scan];
}

- (void)centralManager:(CBCentralManager *)central
 didDisconnectPeripheral:(CBPeripheral *)peripheral
                  error:(NSError *)error {
    NSLog(@"StickS3 disconnected. Scanning again...");
    self.peripheral = nil;
    self.characteristic = nil;
    self.lastEvent = @"";
    [self scan];
}

- (void)peripheral:(CBPeripheral *)peripheral didDiscoverServices:(NSError *)error {
    if (error) {
        NSLog(@"Service discovery failed: %@", error.localizedDescription);
        return;
    }
    for (CBService *service in peripheral.services) {
        if ([service.UUID isEqual:[CBUUID UUIDWithString:ServiceUUID]]) {
            [peripheral discoverCharacteristics:@[[CBUUID UUIDWithString:CaptureUUID]] forService:service];
            return;
        }
    }
}

- (void)peripheral:(CBPeripheral *)peripheral
 didDiscoverCharacteristicsForService:(CBService *)service
              error:(NSError *)error {
    if (error) {
        NSLog(@"Characteristic discovery failed: %@", error.localizedDescription);
        return;
    }
    for (CBCharacteristic *characteristic in service.characteristics) {
        if ([characteristic.UUID isEqual:[CBUUID UUIDWithString:CaptureUUID]]) {
            self.characteristic = characteristic;
            [peripheral setNotifyValue:YES forCharacteristic:characteristic];
            return;
        }
    }
    NSLog(@"Capture characteristic was not found.");
}

- (void)peripheral:(CBPeripheral *)peripheral
 didUpdateValueForCharacteristic:(CBCharacteristic *)characteristic
              error:(NSError *)error {
    if (error || !characteristic.value) return;
    NSString *event = [[NSString alloc] initWithData:characteristic.value
                                            encoding:NSUTF8StringEncoding];
    if (![event hasPrefix:@"capture:"] || [event isEqualToString:self.lastEvent]) return;
    self.lastEvent = event;
    NSLog(@"Button pressed. Capturing the Mac...");
    [self capture];
}

- (void)capture {
    NSURL *url = [NSURL URLWithString:[self.daemon stringByAppendingString:
                                      @"/worklog-capture/api/capture"]];
    if (!url) {
        [self acknowledge:@"error:bad-url"];
        return;
    }

    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.HTTPMethod = @"POST";
    request.timeoutInterval = 15;
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    if (self.token.length > 0) {
        [request setValue:[@"Bearer " stringByAppendingString:self.token]
       forHTTPHeaderField:@"Authorization"];
    }
    request.HTTPBody = [NSJSONSerialization dataWithJSONObject:@{
        @"source": @"bluetooth",
        @"note": @"StickS3 BLE button"
    } options:0 error:nil];

    __weak typeof(self) weakSelf = self;
    NSURLSessionDataTask *task = [NSURLSession.sharedSession
        dataTaskWithRequest:request
          completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        WorklogBridge *strongSelf = weakSelf;
        if (!strongSelf) return;
        if (error) {
            [strongSelf acknowledge:@"error:daemon-offline"];
            return;
        }
        NSInteger status = [(NSHTTPURLResponse *)response statusCode];
        if (status == 401 || status == 403) {
            [strongSelf acknowledge:@"error:auth"];
            return;
        }
        if (status == 404) {
            [strongSelf acknowledge:@"error:app-missing"];
            return;
        }
        NSDictionary *json = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        if (status != 200 || ![json[@"ok"] boolValue]) {
            [strongSelf acknowledge:[NSString stringWithFormat:@"error:http-%ld", (long)status]];
            return;
        }
        if ([json[@"has_image"] boolValue]) {
            NSString *app = json[@"app"] ?: @"captured";
            if (app.length > 36) app = [app substringToIndex:36];
            [strongSelf acknowledge:[@"ok:" stringByAppendingString:app]];
            NSLog(@"Captured and queued for review.");
        } else {
            [strongSelf acknowledge:@"warn:no-image"];
            NSLog(@"Queued without an image. Check macOS Screen Recording permission.");
        }
    }];
    [task resume];
}

- (void)acknowledge:(NSString *)message {
    dispatch_async(dispatch_get_main_queue(), ^{
        if (!self.peripheral || !self.characteristic) return;
        NSData *data = [message dataUsingEncoding:NSUTF8StringEncoding];
        [self.peripheral writeValue:data
                  forCharacteristic:self.characteristic
                               type:CBCharacteristicWriteWithResponse];
    });
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *daemon = @"http://127.0.0.1:9000";
        NSString *config = [NSFileManager.defaultManager.currentDirectoryPath
                            stringByAppendingPathComponent:@"emptyos.toml"];
        NSArray<NSString *> *arguments = NSProcessInfo.processInfo.arguments;
        for (NSUInteger index = 1; index < arguments.count; index++) {
            if ([arguments[index] isEqualToString:@"--daemon"] && index + 1 < arguments.count) {
                daemon = arguments[++index];
            } else if ([arguments[index] isEqualToString:@"--config"] && index + 1 < arguments.count) {
                config = arguments[++index];
            }
        }
        WorklogBridge *bridge = [[WorklogBridge alloc] initWithDaemon:daemon config:config];
        (void)bridge;
        [NSRunLoop.mainRunLoop run];
    }
    return 0;
}
