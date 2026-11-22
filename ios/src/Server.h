#import <Foundation/Foundation.h>
@class Library;

@interface Server : NSObject
@property (nonatomic, copy, readonly) NSString *origin;
+ (NSString *)normalizedOrigin:(NSString *)input;
+ (BOOL)storeToken:(NSString *)token forOrigin:(NSString *)origin;
- (instancetype)initWithOrigin:(NSString *)origin;
- (void)catalog:(void (^)(NSArray *playlists, NSString *error))done;
- (void)importLink:(NSString *)link completion:(void (^)(NSDictionary *report, NSString *error))done;
- (NSURLSessionDownloadTask *)download:(NSDictionary *)track into:(Library *)library completion:(void (^)(NSString *error))done;
@end
