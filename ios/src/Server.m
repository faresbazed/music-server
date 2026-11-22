#import "Server.h"
#import "Library.h"
#import <Security/Security.h>

static NSDictionary *TokenQuery(NSString *origin) {
    return @{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService:@"OfflineMusic.LibraryToken", (__bridge id)kSecAttrAccount:origin};
}

@interface RequestPolicy : NSObject <NSURLSessionTaskDelegate>
@end
@implementation RequestPolicy
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
    completionHandler:(void (^)(NSURLRequest *))completionHandler {
    completionHandler(nil); // Never forward X-Library-Token, even to a redirected HTTPS host.
}
@end

@implementation Server {
    NSURLSession *_session;
}

+ (NSString *)normalizedOrigin:(NSString *)input {
    NSString *raw = [input stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    NSURLComponents *c = [NSURLComponents componentsWithString:raw];
    if (!c.URL || ![c.scheme.lowercaseString isEqual:@"https"] || !c.host.length ||
        c.user != nil || c.password != nil || c.query != nil || c.fragment != nil ||
        (c.percentEncodedPath.length && ![c.percentEncodedPath isEqual:@"/"]) ||
        (c.port && (c.port.integerValue < 1 || c.port.integerValue > 65535))) return nil;
    c.scheme = @"https";
    c.host = c.host.lowercaseString;
    c.path = @"";
    if (c.port.integerValue == 443) c.port = nil;
    return c.URL.absoluteString;
}

+ (BOOL)storeToken:(NSString *)token forOrigin:(NSString *)origin {
    if (!token.length || [token rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location != NSNotFound) return NO;
    NSDictionary *query = TokenQuery(origin);
    NSDictionary *attrs = @{(__bridge id)kSecValueData:[token dataUsingEncoding:NSUTF8StringEncoding],
        (__bridge id)kSecAttrAccessible:(__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly};
    OSStatus result = SecItemUpdate((__bridge CFDictionaryRef)query, (__bridge CFDictionaryRef)attrs);
    if (result == errSecItemNotFound) {
        NSMutableDictionary *item = [query mutableCopy];
        [item addEntriesFromDictionary:attrs];
        result = SecItemAdd((__bridge CFDictionaryRef)item, NULL);
    }
    return result == errSecSuccess;
}

- (instancetype)initWithOrigin:(NSString *)origin {
    if ((self = [super init])) {
        _origin = [[Server normalizedOrigin:origin] copy];
        if (!_origin) return nil;
        NSURLSessionConfiguration *config = [NSURLSessionConfiguration ephemeralSessionConfiguration];
        config.URLCache = nil;
        config.URLCredentialStorage = nil;
        config.HTTPCookieStorage = nil;
        config.HTTPShouldSetCookies = NO;
        config.timeoutIntervalForRequest = 90;
        _session = [NSURLSession sessionWithConfiguration:config delegate:[RequestPolicy new]
                                           delegateQueue:[NSOperationQueue mainQueue]];
    }
    return self;
}

- (void)dealloc { [_session invalidateAndCancel]; }

- (NSMutableURLRequest *)request:(NSString *)path {
    NSMutableDictionary *query = [TokenQuery(self.origin) mutableCopy];
    query[(__bridge id)kSecReturnData] = @YES;
    CFTypeRef raw = NULL;
    if (SecItemCopyMatching((__bridge CFDictionaryRef)query, &raw) != errSecSuccess) return nil;
    NSString *token = [[NSString alloc] initWithData:CFBridgingRelease(raw) encoding:NSUTF8StringEncoding];
    if (!token.length) return nil;
    NSMutableURLRequest *r = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[self.origin stringByAppendingString:path]]];
    [r setValue:token forHTTPHeaderField:@"X-Library-Token"];
    return r;
}

static NSString *ResponseError(NSURLResponse *response, NSError *error, NSDictionary *body) {
    if (error) return error.localizedDescription;
    NSInteger status = [(NSHTTPURLResponse *)response statusCode];
    if (status == 200) return nil;
    if (status == 401 || status == 403) return @"Access denied. Check your library token in Server settings.";
    if (status >= 300 && status < 400) return @"The server redirected this request. Enter its final HTTPS address.";
    NSString *detail = Text(body[@"detail"], Text(body[@"error"], nil));
    return detail ?: [NSString stringWithFormat:@"The server returned HTTP %ld.", (long)status];
}

- (void)json:(NSString *)path post:(NSDictionary *)body completion:(void (^)(NSDictionary *, NSString *))done {
    NSMutableURLRequest *r = [self request:path];
    if (!r) { done(nil, @"Token unavailable. Unlock this iPad or enter the token in Server settings."); return; }
    [r setValue:@"application/json" forHTTPHeaderField:@"Accept"];
    if (body) {
        r.HTTPMethod = @"POST";
        [r setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
        r.HTTPBody = [NSJSONSerialization dataWithJSONObject:body options:0 error:NULL];
    }
    [[_session dataTaskWithRequest:r completionHandler:^(NSData *data, NSURLResponse *res, NSError *err) {
        id value = data.length ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
        NSDictionary *obj = [value isKindOfClass:NSDictionary.class] ? value : nil;
        NSString *failure = ResponseError(res, err, obj);
        done(obj, failure ?: (obj ? nil : @"The server did not return JSON."));
    }] resume];
}

- (void)catalog:(void (^)(NSArray *, NSString *))done {
    [self json:@"/api/catalog?enrich=0" post:nil completion:^(NSDictionary *obj, NSString *error) {
        NSArray *lists = obj[@"playlists"];
        BOOL valid = [lists isKindOfClass:NSArray.class];
        if (valid) for (id p in lists) {
            if (![p isKindOfClass:NSDictionary.class] || ![p[@"tracks"] isKindOfClass:NSArray.class]) { valid = NO; break; }
            for (id t in p[@"tracks"]) if (![t isKindOfClass:NSDictionary.class]) { valid = NO; break; }
            if (!valid) break;
        }
        done(valid ? lists : nil, error ?: (valid ? nil : @"Unexpected catalog format."));
    }];
}

- (void)importLink:(NSString *)link completion:(void (^)(NSDictionary *, NSString *))done {
    [self json:@"/api/import/spotify" post:@{@"url":link} completion:^(NSDictionary *obj, NSString *error) {
        NSDictionary *report = obj[@"report"];
        BOOL ok = [obj[@"ok"] isEqual:@YES] && [report isKindOfClass:NSDictionary.class] &&
            [report[@"matched"] isKindOfClass:NSNumber.class] && [report[@"missing_count"] isKindOfClass:NSNumber.class];
        done(ok ? report : nil, error ?: (ok ? nil : @"Unexpected import report."));
    }];
}

- (NSURLSessionDownloadTask *)download:(NSDictionary *)track into:(Library *)library completion:(void (^)(NSString *))done {
    NSString *tid = MatchedID(track);
    NSMutableURLRequest *r = tid ? [self request:[@"/api/download/" stringByAppendingString:tid]] : nil;
    if (!r) { done(tid ? @"Library token unavailable." : @"No matching file."); return nil; }
    NSURLSessionDownloadTask *task = [_session downloadTaskWithRequest:r completionHandler:^(NSURL *file, NSURLResponse *res, NSError *err) {
        NSString *failure = ResponseError(res, err, nil);
        NSString *mime = res.MIMEType.lowercaseString;
        if (!failure && (!file || !( [mime hasPrefix:@"audio/"] || [mime isEqual:@"application/octet-stream"] ||
                                     [mime isEqual:@"application/ogg"])))
            failure = @"The server did not return an audio file.";
        NSError *saveError = nil;
        if (!failure && ![library saveFile:file track:track origin:self.origin error:&saveError])
            failure = saveError.localizedDescription ?: @"Could not save the track.";
        done(failure);
    }];
    [task resume];
    return task;
}
@end
